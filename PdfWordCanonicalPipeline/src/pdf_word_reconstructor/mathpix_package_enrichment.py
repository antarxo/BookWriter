from __future__ import annotations

from typing import Any

from .mathpix_package_input import summarize_mathpix_package


VERSION = "mathpix-package-enrichment-0.1"


def enrich_with_mathpix_package(
    target: dict[str, Any],
    package_map: dict[str, Any] | None,
    *,
    attach_page_assets: bool = True,
) -> dict[str, Any]:
    """Attach lossless Mathpix package evidence to an existing canonical map.

    This is deliberately non-destructive: existing flow/columns/tables/visual
    groups are untouched. The function only adds package evidence so later
    adapters can consume it without re-reading the API package.
    """
    if not package_map:
        target["mathpixPackageSummary"] = {"available": False}
        return target

    target["mathpixPackageSummary"] = summarize_mathpix_package(package_map)
    target["mathpixPackageMap"] = package_map
    target["mathpixMarkdownMap"] = {
        "version": package_map.get("version"),
        "policy": "canonical MMD and packaged MMD references are retained separately",
        **(package_map.get("markdown") or {}),
    }
    target["mathpixAssetMap"] = {
        "version": package_map.get("version"),
        "policy": "every packaged asset is retained, including assets not referenced by canonical MMD",
        "summary": {
            "assetCount": ((package_map.get("audit") or {}).get("assetCount")),
            "unreferencedPackagedAssetCount": ((package_map.get("audit") or {}).get("unreferencedPackagedAssetCount")),
            "packagedMmdResolvedReferenceCount": ((package_map.get("audit") or {}).get("packagedMmdResolvedReferenceCount")),
        },
        "assets": package_map.get("assets") or [],
    }
    target["mathpixPackageCompletenessAudit"] = package_map.get("audit") or {}

    if attach_page_assets:
        assets_by_page = {
            int(page.get("page") or 0): page
            for page in package_map.get("pages", []) or []
            if int(page.get("page") or 0) > 0
        }
        for page in target.get("pages", []) or []:
            page_no = int(page.get("page") or 0)
            package_page = assets_by_page.get(page_no)
            if package_page is not None:
                page["mathpixAssetPageMap"] = package_page

    target.setdefault("mathpixEnrichment", {})["packageEvidenceVersion"] = VERSION
    target["mathpixEnrichment"]["packageEvidenceAvailable"] = True
    target["mathpixEnrichment"]["packageAuditStatus"] = (package_map.get("audit") or {}).get("status")
    return target
