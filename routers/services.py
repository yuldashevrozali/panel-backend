from fastapi import APIRouter, HTTPException, Query

from schemas.services import ServiceRead
from services.onepanel import OnePanelError, onepanel

router = APIRouter(prefix="/services", tags=["Services"])


def provider_error(exc: OnePanelError) -> HTTPException:
    return HTTPException(status_code=503 if exc.unavailable else 502, detail=str(exc))


@router.get("", response_model=list[ServiceRead])
def list_services(
    category: str | None = Query(default=None, max_length=200),
    search: str | None = Query(default=None, max_length=200),
    service_id: str | None = Query(default=None, max_length=100),
):
    try:
        services = onepanel.get_services()
    except OnePanelError as exc:
        raise provider_error(exc) from exc
    if service_id is not None:
        services = [service for service in services if service["service"] == service_id]
    if category:
        term = category.casefold()
        services = [
            service for service in services if term in service["category"].casefold()
        ]
    if search:
        term = search.casefold()
        services = [
            service
            for service in services
            if term in service["name"].casefold()
            or term in service["category"].casefold()
        ]
    return services
