"""One answer for both cases: JSON with the re-rendered part for jQuery, or a
redirect (with a message) when the form was sent without JavaScript."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def respond(request, *, redirect_to, template=None, context=None, target=None, msg="", error=False):
    if is_ajax(request):
        data = {"msg": msg, "error": error}
        if template:
            data["html"] = render_to_string(template, context or {}, request=request)
            data["target"] = target
        return JsonResponse(data, status=400 if error and not template else 200)
    if msg:
        (messages.error if error else messages.success)(request, msg)
    return redirect(redirect_to)
