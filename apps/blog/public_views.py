"""Public blog — the platform's own marketing content, apex host only (same
gate as ``apps.core.views.legal_page``/``pricing``: a resolved ``request.project``
means this is a tenant's own domain, send it to the storefront instead)."""

import json

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from .models import BlogCategory, BlogPost, BlogTag, published_posts

PER_PAGE = 9


def _not_a_tenant_host(request):
    return not getattr(request, "project", None)


def _list_context(request, request_posts, *, heading, description):
    page = Paginator(request_posts, PER_PAGE).get_page(request.GET.get("page"))
    return {
        "page_obj": page,
        "posts": page.object_list,
        "heading": heading,
        "list_description": description,
        "categories": BlogCategory.objects.filter(posts__is_published=True).distinct(),
    }


def blog_index(request):
    if not _not_a_tenant_host(request):
        return redirect("/app/")
    from apps.core.brand import brand_for_request

    brand = brand_for_request(request)
    ctx = _list_context(
        request, published_posts(),
        heading="Blog",
        description=f"Guides and updates from {brand.name} on running an online store in India.",
    )
    return render(request, "marketing/blog_list.jinja", ctx)


def blog_category(request, slug):
    if not _not_a_tenant_host(request):
        return redirect("/app/")
    category = get_object_or_404(BlogCategory, slug=slug)
    ctx = _list_context(
        request, published_posts().filter(category=category),
        heading=category.name,
        description=f"Posts filed under {category.name}.",
    )
    ctx["category"] = category
    return render(request, "marketing/blog_list.jinja", ctx)


def blog_tag(request, slug):
    if not _not_a_tenant_host(request):
        return redirect("/app/")
    tag = get_object_or_404(BlogTag, slug=slug)
    ctx = _list_context(
        request, published_posts().filter(tags=tag),
        heading=f"#{tag.name}",
        description=f"Posts tagged {tag.name}.",
    )
    ctx["tag"] = tag
    return render(request, "marketing/blog_list.jinja", ctx)


def blog_detail(request, slug):
    if not _not_a_tenant_host(request):
        return redirect("/app/")
    post = get_object_or_404(BlogPost, slug=slug)
    if not post.is_live:
        raise Http404
    from apps.core.brand import brand_for_request

    brand = brand_for_request(request)
    base = f"{request.scheme}://{request.get_host()}"
    jsonld = [{
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": post.title,
        "description": post.seo_description or post.excerpt,
        "datePublished": post.published_at.isoformat() if post.published_at else None,
        "dateModified": post.updated_at.isoformat(),
        "mainEntityOfPage": f"{base}/blog/{post.slug}/",
        "publisher": {"@type": "Organization", "name": brand.name, "url": base + "/"},
        "author": {"@type": "Person", "name": post.author.name} if post.author
        else {"@type": "Organization", "name": brand.name},
    }, {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": base + "/blog/"},
            {"@type": "ListItem", "position": 3, "name": post.title, "item": f"{base}/blog/{post.slug}/"},
        ],
    }]
    if post.cover_image:
        jsonld[0]["image"] = base + post.cover_image.url
    return render(request, "marketing/blog_detail.jinja", {
        "post": post,
        "related": post.related(),
        "post_jsonld": json.dumps(jsonld, separators=(",", ":"), ensure_ascii=False, default=str),
    })
