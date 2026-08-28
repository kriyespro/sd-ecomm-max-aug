def tenant(request):
    """Expose the resolved project to every template."""
    return {"project": getattr(request, "project", None)}
