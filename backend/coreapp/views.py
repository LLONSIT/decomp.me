from coreapp.asm_diff_wrapper import AsmDifferWrapper
from coreapp.m2c_wrapper import M2CWrapper
from coreapp.compiler_wrapper import CompilerWrapper
from coreapp.serializers import ScratchSerializer
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.utils.crypto import get_random_string
import logging

import hashlib

from .models import Profile, Asm, Scratch

def index(request):
    return HttpResponse("This is the index page.")

def get_db_asm(request_asm) -> Asm:
    h = hashlib.sha256(request_asm.encode()).hexdigest()

    db_asm = Asm.objects.filter(hash=h)

    if not db_asm:
        ret = Asm(hash=h, data=request_asm)
        ret.save()
    else:
        ret = db_asm.first()
    
    return ret


@api_view(["GET", "POST", "PATCH"])
def scratch(request, slug=None):
    """
    Get, create, or update a scratch
    """

    if request.method == "GET":
        if not slug:
            return Response("Missing slug", status=status.HTTP_400_BAD_REQUEST)

        db_scratch = get_object_or_404(Scratch, slug=slug)

        if not db_scratch.owner:
            # Give ownership to this profile
            profile = Profile.objects.filter(id=request.session.get("profile", None)).first()

            if not profile:
                profile = Profile()
                profile.save()
                request.session["profile"] = profile.id

            logging.debug(f"Granting ownership of scratch {db_scratch} to {profile}")

            db_scratch.owner = profile
            db_scratch.save()

        return Response({
            "scratch": ScratchSerializer(db_scratch).data,
            "is_yours": db_scratch.owner.id == request.session.get("profile", None),   
        })
    
    elif request.method == "POST":
        data = request.data

        if slug:
            return Response({"error": "Not allowed to POST with slug"}, status=status.HTTP_400_BAD_REQUEST)

        if "target_asm" not in data:
            return Response({"error": "Missing target_asm"}, status=status.HTTP_400_BAD_REQUEST)

        data["slug"] = get_random_string(length=5)

        asm = get_db_asm(data["target_asm"])
        del data["target_asm"]

        compiler = request.data["compiler"]
        as_opts = request.data["as_opts"]

        assembly = CompilerWrapper.assemble_asm(compiler, as_opts, asm)
        if assembly:
            data["target_assembly"] = assembly.pk
        else:
            return Response({"error": "Error when assembling target asm"}, status=status.HTTP_400_BAD_REQUEST)

        m2c_stab = M2CWrapper.decompile(asm.data)
        data["source_code"] = m2c_stab if m2c_stab else "void func() {}\n"

        serializer = ScratchSerializer(data=data)
        if serializer.is_valid():
            if serializer.context:
                serializer.original_context = serializer.context
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == "PATCH":
        if not slug:
            return Response({"error": "Missing slug"}, status=status.HTTP_400_BAD_REQUEST)
        
        required_params = ["compiler", "cpp_opts", "as_opts", "cc_opts", "source_code", "context"]

        for param in required_params:
            if param not in request.data:
                return Response({"error": f"Missing parameter: {param}"}, status=status.HTTP_400_BAD_REQUEST)

        db_scratch = get_object_or_404(Scratch, slug=slug)

        if db_scratch.owner and db_scratch.owner.id != request.session.get("profile", None):
            return Response(status=status.HTTP_403_FORBIDDEN)

        # TODO validate
        db_scratch.compiler = request.data["compiler"]
        db_scratch.cpp_opts = request.data["cpp_opts"]
        db_scratch.as_opts = request.data["as_opts"]
        db_scratch.cc_opts = request.data["cc_opts"]
        db_scratch.source_code = request.data["source_code"]
        db_scratch.context = request.data["context"]
        db_scratch.save()
        return Response(status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def compile(request, slug):
    required_params = ["compiler", "cpp_opts", "as_opts", "cc_opts", "source_code", "context"]

    for param in required_params:
        if param not in request.data:
            return Response({"error": f"Missing parameter: {param}"}, status=status.HTTP_400_BAD_REQUEST)
    
    # TODO validate
    compiler = request.data["compiler"]
    cpp_opts = request.data["cpp_opts"]
    as_opts = request.data["as_opts"]
    cc_opts = request.data["cc_opts"]
    code = request.data["source_code"]
    context = request.data["context"]

    scratch = Scratch.objects.get(slug=slug)

    # Get the context from the backend if it's not provided
    if not context or context.isspace():
        context = scratch.context
    
    compilation, errors = CompilerWrapper.compile_code(compiler, cpp_opts, as_opts, cc_opts, code, context)

    diff_output = ""
    if compilation:
        diff_output = AsmDifferWrapper.diff(scratch.target_assembly, compilation)

    response_obj = {
        "diff_output": diff_output,
        "errors": errors,
    }
        
    return Response(response_obj)
