"""Google Admin SDK Directory MCP tools."""

import asyncio
import logging
import secrets
from typing import Any, Dict, List, Literal, Optional

from googleapiclient.discovery import Resource
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

from auth.scopes import (
    ADMIN_GROUP_MEMBER_READONLY_SCOPE,
    ADMIN_GROUP_MEMBER_SCOPE,
    ADMIN_GROUP_READONLY_SCOPE,
    ADMIN_GROUP_SCOPE,
    ADMIN_ORGUNIT_READONLY_SCOPE,
    ADMIN_ORGUNIT_SCOPE,
    ADMIN_USER_ALIAS_SCOPE,
    ADMIN_USER_READONLY_SCOPE,
    ADMIN_USER_SCOPE,
)
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger("workspace_mcp.admin")


class AdminUserName(BaseModel):
    """Typed Directory user name fields."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    given_name: Optional[str] = Field(default=None, alias="givenName")
    family_name: Optional[str] = Field(default=None, alias="familyName")


class AdminUserBody(BaseModel):
    """Supported user fields; plaintext credentials are intentionally excluded."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    primary_email: Optional[EmailStr] = Field(default=None, alias="primaryEmail")
    name: Optional[AdminUserName] = None
    org_unit_path: Optional[str] = Field(default=None, alias="orgUnitPath")
    recovery_email: Optional[EmailStr] = Field(default=None, alias="recoveryEmail")
    recovery_phone: Optional[str] = Field(default=None, alias="recoveryPhone")
    emails: Optional[List[Dict[str, Any]]] = None
    phones: Optional[List[Dict[str, Any]]] = None
    organizations: Optional[List[Dict[str, Any]]] = None
    custom_schemas: Optional[Dict[str, Any]] = Field(
        default=None, alias="customSchemas"
    )


class AdminGroupBody(BaseModel):
    """Typed fields accepted by Directory group create/update operations."""

    model_config = ConfigDict(extra="forbid")

    email: Optional[EmailStr] = None
    name: Optional[str] = None
    description: Optional[str] = None


class AdminMemberBody(BaseModel):
    """Validated group membership fields."""

    email: EmailStr
    role: Literal["MEMBER", "MANAGER", "OWNER"] = "MEMBER"


class AdminAliasBody(BaseModel):
    """Validated alias request body."""

    alias: EmailStr


class AdminOrgUnitBody(BaseModel):
    """Typed fields accepted by org-unit create/update operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: Optional[str] = None
    parent_org_unit_path: Optional[str] = Field(
        default=None, alias="parentOrgUnitPath"
    )
    description: Optional[str] = None
    block_inheritance: Optional[bool] = Field(default=None, alias="blockInheritance")


def _validate_user_body(user: Any) -> AdminUserBody:
    if isinstance(user, dict) and "password" in user:
        raise UserInputError(
            "The user body must not include a password; credentials are provisioned "
            "internally for create operations."
        )
    try:
        return user if isinstance(user, AdminUserBody) else AdminUserBody.model_validate(user)
    except ValidationError:
        raise UserInputError("Invalid user body fields or values.") from None


def _validate_group_body(group: Any) -> AdminGroupBody:
    try:
        return (
            group
            if isinstance(group, AdminGroupBody)
            else AdminGroupBody.model_validate(group)
        )
    except ValidationError:
        raise UserInputError("Invalid group body fields or values.") from None


def _validate_org_unit_body(org_unit: Any) -> AdminOrgUnitBody:
    try:
        return (
            org_unit
            if isinstance(org_unit, AdminOrgUnitBody)
            else AdminOrgUnitBody.model_validate(org_unit)
        )
    except ValidationError:
        raise UserInputError("Invalid org_unit body fields or values.") from None


@server.tool(
    title="List Admin Users",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_USER_READONLY_SCOPE)
@handle_http_errors("list_admin_users", is_read_only=True, service_type="admin")
async def list_admin_users(
    service: Resource,
    user_google_email: str,
    customer: Optional[str] = "my_customer",
    domain: Optional[str] = None,
    query: Optional[str] = None,
    order_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    max_results: int = 100,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List users in a Workspace tenant and retain the continuation token."""
    logger.info("[list_admin_users] Listing directory users")
    params: Dict[str, Any] = {"maxResults": max_results}
    if domain:
        params["domain"] = domain
    elif customer:
        params["customer"] = customer
    if query:
        params["query"] = query
    if order_by:
        params["orderBy"] = order_by
    if sort_order:
        params["sortOrder"] = sort_order
    if page_token:
        params["pageToken"] = page_token

    response = await asyncio.to_thread(service.users().list(**params).execute)
    result: Dict[str, Any] = {"users": response.get("users", [])}
    if "nextPageToken" in response:
        result["nextPageToken"] = response["nextPageToken"]
    return result


@server.tool(
    title="Get Admin User",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_USER_READONLY_SCOPE)
@handle_http_errors("get_admin_user", is_read_only=True, service_type="admin")
async def get_admin_user(
    service: Resource,
    user_google_email: str,
    user_key: str,
    projection: Optional[str] = None,
) -> Dict[str, Any]:
    """Get one Directory user by primary email, alias, or immutable ID."""
    logger.info("[get_admin_user] Getting directory user")
    params: Dict[str, Any] = {"userKey": user_key}
    if projection:
        params["projection"] = projection
    return await asyncio.to_thread(service.users().get(**params).execute)


@server.tool(
    title="Manage Admin User",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_USER_SCOPE)
@handle_http_errors("manage_admin_user", service_type="admin")
async def manage_admin_user(
    service: Resource,
    user_google_email: str,
    action: str,
    user_key: Optional[str] = None,
    user: Optional[AdminUserBody] = None,
) -> Dict[str, Any]:
    """Create or otherwise manage a Directory user without accepting credentials."""
    logger.info("[manage_admin_user] Action: %s", action)
    valid_actions = {"create", "update", "suspend", "unsuspend", "delete"}
    if action not in valid_actions:
        raise UserInputError(
            "Invalid action. Must be one of: create, update, suspend, unsuspend, delete"
        )

    if action == "delete":
        if not user_key:
            raise UserInputError("user_key is required for delete")
        await asyncio.to_thread(service.users().delete(userKey=user_key).execute)
        return {"status": "deleted", "userKey": user_key}

    if action in {"suspend", "unsuspend"}:
        if not user_key:
            raise UserInputError(f"user_key is required for {action}")
        return await asyncio.to_thread(
            service.users()
            .update(userKey=user_key, body={"suspended": action == "suspend"})
            .execute
        )

    if user is None:
        raise UserInputError(f"user is required for {action}")

    validated = _validate_user_body(user)
    body = validated.model_dump(by_alias=True, exclude_none=True)

    if action == "update":
        if not user_key:
            raise UserInputError("user_key is required for update")
        if not body:
            raise UserInputError("user must contain at least one field for update")
        return await asyncio.to_thread(
            service.users().update(userKey=user_key, body=body).execute
        )

    if not validated.primary_email:
        raise UserInputError("user.primaryEmail is required for create")
    if (
        not validated.name
        or not validated.name.given_name
        or not validated.name.family_name
    ):
        raise UserInputError(
            "user.name.givenName and user.name.familyName are required for create"
        )

    body["password"] = secrets.token_urlsafe(32)
    body["changePasswordAtNextLogin"] = True
    response = await asyncio.to_thread(service.users().insert(body=body).execute)
    sanitized = {key: value for key, value in response.items() if key != "password"}
    return {
        "user": sanitized,
        "credentialProvisioning": (
            "A temporary credential was generated internally and was not returned. "
            "An administrator must reset or provision credentials out-of-band before "
            "the user's first login."
        ),
    }


@server.tool(
    title="List Admin Groups",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_GROUP_READONLY_SCOPE)
@handle_http_errors("list_admin_groups", is_read_only=True, service_type="admin")
async def list_admin_groups(
    service: Resource,
    user_google_email: str,
    customer: Optional[str] = "my_customer",
    domain: Optional[str] = None,
    user_key: Optional[str] = None,
    query: Optional[str] = None,
    max_results: int = 200,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List Directory groups and retain the continuation token."""
    logger.info("[list_admin_groups] Listing directory groups")
    params: Dict[str, Any] = {"maxResults": max_results}
    if domain:
        params["domain"] = domain
    elif customer:
        params["customer"] = customer
    if user_key:
        params["userKey"] = user_key
    if query:
        params["query"] = query
    if page_token:
        params["pageToken"] = page_token

    response = await asyncio.to_thread(service.groups().list(**params).execute)
    result: Dict[str, Any] = {"groups": response.get("groups", [])}
    if "nextPageToken" in response:
        result["nextPageToken"] = response["nextPageToken"]
    return result


@server.tool(
    title="Get Admin Group",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_GROUP_READONLY_SCOPE)
@handle_http_errors("get_admin_group", is_read_only=True, service_type="admin")
async def get_admin_group(
    service: Resource,
    user_google_email: str,
    group_key: str,
) -> Dict[str, Any]:
    """Get one Directory group by email, alias, or immutable ID."""
    logger.info("[get_admin_group] Getting directory group")
    return await asyncio.to_thread(
        service.groups().get(groupKey=group_key).execute
    )


@server.tool(
    title="Manage Admin Group",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_GROUP_SCOPE)
@handle_http_errors("manage_admin_group", service_type="admin")
async def manage_admin_group(
    service: Resource,
    user_google_email: str,
    action: str,
    group_key: Optional[str] = None,
    group: Optional[AdminGroupBody] = None,
) -> Dict[str, Any]:
    """Create or otherwise manage a Directory group."""
    logger.info("[manage_admin_group] Action: %s", action)
    if action not in {"create", "update", "delete"}:
        raise UserInputError("Invalid action. Must be one of: create, update, delete")

    if action == "delete":
        if not group_key:
            raise UserInputError("group_key is required for delete")
        await asyncio.to_thread(service.groups().delete(groupKey=group_key).execute)
        return {"status": "deleted", "groupKey": group_key}

    if group is None:
        raise UserInputError(f"group is required for {action}")
    validated = _validate_group_body(group)
    body = validated.model_dump(exclude_none=True)

    if action == "update":
        if not group_key:
            raise UserInputError("group_key is required for update")
        if not body:
            raise UserInputError("group must contain at least one field for update")
        return await asyncio.to_thread(
            service.groups().update(groupKey=group_key, body=body).execute
        )

    if not validated.email:
        raise UserInputError("group.email is required for create")
    return await asyncio.to_thread(service.groups().insert(body=body).execute)


@server.tool(
    title="List Admin Group Members",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_GROUP_MEMBER_READONLY_SCOPE)
@handle_http_errors(
    "list_admin_group_members", is_read_only=True, service_type="admin"
)
async def list_admin_group_members(
    service: Resource,
    user_google_email: str,
    group_key: str,
    roles: Optional[str] = None,
    max_results: int = 200,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List members of a Directory group and retain the continuation token."""
    logger.info("[list_admin_group_members] Listing group members")
    params: Dict[str, Any] = {"groupKey": group_key, "maxResults": max_results}
    if roles:
        params["roles"] = roles
    if page_token:
        params["pageToken"] = page_token
    response = await asyncio.to_thread(service.members().list(**params).execute)
    result: Dict[str, Any] = {"members": response.get("members", [])}
    if "nextPageToken" in response:
        result["nextPageToken"] = response["nextPageToken"]
    return result


@server.tool(
    title="Manage Admin Group Membership",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_GROUP_MEMBER_SCOPE)
@handle_http_errors("manage_admin_group_membership", service_type="admin")
async def manage_admin_group_membership(
    service: Resource,
    user_google_email: str,
    action: str,
    group_key: str,
    member_email: Optional[EmailStr] = None,
    member_key: Optional[str] = None,
    role: str = "MEMBER",
) -> Dict[str, Any]:
    """Add or otherwise manage a member of a Directory group."""
    logger.info("[manage_admin_group_membership] Action: %s", action)
    if action not in {"add", "update_role", "remove"}:
        raise UserInputError("Invalid action. Must be one of: add, update_role, remove")
    if not group_key:
        raise UserInputError(f"group_key is required for {action}")

    if action == "remove":
        if not member_key:
            raise UserInputError("member_key is required for remove")
        await asyncio.to_thread(
            service.members().delete(groupKey=group_key, memberKey=member_key).execute
        )
        return {
            "status": "removed",
            "groupKey": group_key,
            "memberKey": member_key,
        }

    if role not in {"MEMBER", "MANAGER", "OWNER"}:
        raise UserInputError("role must be one of: MEMBER, MANAGER, OWNER")

    if action == "update_role":
        if not member_key:
            raise UserInputError("member_key is required for update_role")
        return await asyncio.to_thread(
            service.members()
            .update(groupKey=group_key, memberKey=member_key, body={"role": role})
            .execute
        )

    if not member_email:
        raise UserInputError("member_email is required for add")
    try:
        member = AdminMemberBody.model_validate({"email": member_email, "role": role})
    except ValidationError:
        raise UserInputError("member_email or role is invalid.") from None
    body = member.model_dump(mode="json")
    return await asyncio.to_thread(
        service.members().insert(groupKey=group_key, body=body).execute
    )


@server.tool(
    title="Manage Admin Alias",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@require_google_service("admin", [ADMIN_USER_ALIAS_SCOPE, ADMIN_GROUP_SCOPE])
@handle_http_errors("manage_admin_alias", service_type="admin")
async def manage_admin_alias(
    service: Resource,
    user_google_email: str,
    action: str,
    target_type: Literal["user", "group"],
    target_key: str,
    alias: EmailStr,
) -> Dict[str, Any]:
    """Add or remove an alias from a Directory user or group."""
    logger.info("[manage_admin_alias] Action: %s; target type: %s", action, target_type)
    if action not in {"add", "remove"}:
        raise UserInputError("Invalid action. Must be one of: add, remove")
    if target_type not in {"user", "group"}:
        raise UserInputError("target_type must be one of: user, group")
    if not target_key:
        raise UserInputError(f"target_key is required for {action}")
    try:
        body = AdminAliasBody.model_validate({"alias": alias}).model_dump(mode="json")
    except ValidationError:
        raise UserInputError("alias must be a valid email address") from None

    if action == "remove":
        if target_type == "user":
            request = service.users().aliases().delete(
                userKey=target_key, alias=body["alias"]
            )
        else:
            request = service.groups().aliases().delete(
                groupKey=target_key, alias=body["alias"]
            )
        await asyncio.to_thread(request.execute)
        return {
            "status": "removed",
            "targetType": target_type,
            "targetKey": target_key,
            "alias": body["alias"],
        }

    if target_type == "user":
        request = service.users().aliases().insert(userKey=target_key, body=body)
    else:
        request = service.groups().aliases().insert(groupKey=target_key, body=body)
    return await asyncio.to_thread(request.execute)


@server.tool(
    title="List Admin Organizational Units",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_ORGUNIT_READONLY_SCOPE)
@handle_http_errors("list_admin_org_units", is_read_only=True, service_type="admin")
async def list_admin_org_units(
    service: Resource,
    user_google_email: str,
    customer_id: str = "my_customer",
    org_unit_path: Optional[str] = None,
    org_unit_type: str = "all",
) -> Dict[str, Any]:
    """List organizational units in a Workspace tenant."""
    logger.info("[list_admin_org_units] Listing organizational units")
    if org_unit_type not in {"all", "children"}:
        raise UserInputError("org_unit_type must be one of: all, children")
    params: Dict[str, Any] = {"customerId": customer_id, "type": org_unit_type}
    if org_unit_path:
        params["orgUnitPath"] = org_unit_path
    response = await asyncio.to_thread(service.orgunits().list(**params).execute)
    return {"organizationUnits": response.get("organizationUnits", [])}


@server.tool(
    title="Manage Admin Organizational Unit",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@require_google_service("admin", ADMIN_ORGUNIT_SCOPE)
@handle_http_errors("manage_admin_org_unit", service_type="admin")
async def manage_admin_org_unit(
    service: Resource,
    user_google_email: str,
    action: str,
    customer_id: str = "my_customer",
    org_unit_path: Optional[str] = None,
    org_unit: Optional[AdminOrgUnitBody] = None,
) -> Dict[str, Any]:
    """Create or otherwise manage a Directory organizational unit."""
    logger.info("[manage_admin_org_unit] Action: %s", action)
    if action not in {"create", "update", "delete"}:
        raise UserInputError("Invalid action. Must be one of: create, update, delete")

    if action == "delete":
        if not org_unit_path:
            raise UserInputError("org_unit_path is required for delete")
        await asyncio.to_thread(
            service.orgunits()
            .delete(customerId=customer_id, orgUnitPath=org_unit_path)
            .execute
        )
        return {"status": "deleted", "orgUnitPath": org_unit_path}

    if org_unit is None:
        raise UserInputError(f"org_unit is required for {action}")
    validated = _validate_org_unit_body(org_unit)
    body = validated.model_dump(by_alias=True, exclude_none=True)

    if action == "update":
        if not org_unit_path:
            raise UserInputError("org_unit_path is required for update")
        if not body:
            raise UserInputError("org_unit must contain at least one field for update")
        return await asyncio.to_thread(
            service.orgunits()
            .update(
                customerId=customer_id,
                orgUnitPath=org_unit_path,
                body=body,
            )
            .execute
        )

    if not validated.name:
        raise UserInputError("org_unit.name is required for create")
    return await asyncio.to_thread(
        service.orgunits().insert(customerId=customer_id, body=body).execute
    )
