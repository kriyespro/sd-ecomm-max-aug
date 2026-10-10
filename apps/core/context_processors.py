def tenant(request):
    """Expose the resolved project and the platform brand to every template."""
    from .brand import brand_for_request

    return {"project": getattr(request, "project", None), "brand": brand_for_request(request)}
