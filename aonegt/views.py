from django.http import JsonResponse


def health_version(request):
    return JsonResponse({"status": "ok", "version": "2026-04-27-1"})
