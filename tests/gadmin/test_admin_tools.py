import importlib.util
import inspect
import logging
from collections import defaultdict

import pytest

from gadmin import admin_tools
from core.utils import UserInputError

EMAIL = "admin@example.com"


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeResource:
    METHODS = {"list", "get", "insert", "update", "delete"}

    def __init__(self, service, path):
        self.service = service
        self.path = path

    def __getattr__(self, name):
        if name not in self.METHODS:
            return lambda: FakeResource(self.service, f"{self.path}.{name}")

        def call(**kwargs):
            operation = f"{self.path}.{name}"
            self.service.calls.append((operation, kwargs))
            return FakeRequest(self.service.responses[operation].pop(0))

        return call


class FakeAdminService:
    def __init__(self, **responses):
        self.responses = defaultdict(list)
        for operation, response in responses.items():
            self.responses[operation.replace("__", ".")].append(response)
        self.calls = []

    def __getattr__(self, name):
        return lambda: FakeResource(self, name)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def test_admin_tools_package_is_available():
    assert importlib.util.find_spec("gadmin.admin_tools") is not None


@pytest.mark.asyncio
async def test_list_admin_users_preserves_pagination_and_forwards_filters():
    assert hasattr(admin_tools, "list_admin_users")
    service = FakeAdminService(
        users__list={"users": [{"id": "u1"}], "nextPageToken": "next-users"}
    )

    result = await _unwrap(admin_tools.list_admin_users)(
        service=service,
        user_google_email=EMAIL,
        customer="my_customer",
        query="isSuspended=false",
        max_results=50,
        page_token="current-users",
    )

    assert result == {"users": [{"id": "u1"}], "nextPageToken": "next-users"}
    assert service.calls == [
        (
            "users.list",
            {
                "customer": "my_customer",
                "maxResults": 50,
                "query": "isSuspended=false",
                "pageToken": "current-users",
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_admin_user_returns_directory_resource():
    assert hasattr(admin_tools, "get_admin_user")
    service = FakeAdminService(users__get={"id": "u1", "primaryEmail": "u@example.com"})

    result = await _unwrap(admin_tools.get_admin_user)(
        service=service,
        user_google_email=EMAIL,
        user_key="u@example.com",
        projection="full",
    )

    assert result == {"id": "u1", "primaryEmail": "u@example.com"}
    assert service.calls == [
        ("users.get", {"userKey": "u@example.com", "projection": "full"})
    ]


@pytest.mark.asyncio
async def test_manage_admin_user_create_generates_unreturned_temporary_password(caplog):
    assert hasattr(admin_tools, "manage_admin_user")
    fn = _unwrap(admin_tools.manage_admin_user)
    assert "password" not in inspect.signature(fn).parameters
    service = FakeAdminService(
        users__insert={"id": "u1", "primaryEmail": "new@example.com"}
    )

    with caplog.at_level(logging.DEBUG, logger="workspace_mcp.admin"):
        result = await fn(
            service=service,
            user_google_email=EMAIL,
            action="create",
            user={
                "primaryEmail": "new@example.com",
                "name": {"givenName": "New", "familyName": "User"},
            },
        )

    operation, kwargs = service.calls[0]
    temporary_password = kwargs["body"]["password"]
    assert operation == "users.insert"
    assert len(temporary_password) >= 32
    assert kwargs["body"]["changePasswordAtNextLogin"] is True
    assert temporary_password not in repr(result)
    assert temporary_password not in caplog.text
    assert result["user"] == {"id": "u1", "primaryEmail": "new@example.com"}
    assert "out-of-band" in result["credentialProvisioning"]


@pytest.mark.asyncio
async def test_manage_admin_user_rejects_password_before_api_call():
    service = FakeAdminService()

    with pytest.raises(UserInputError, match="must not include a password"):
        await _unwrap(admin_tools.manage_admin_user)(
            service=service,
            user_google_email=EMAIL,
            action="create",
            user={
                "primaryEmail": "new@example.com",
                "name": {"givenName": "New", "familyName": "User"},
                "password": "must-never-be-routed",
            },
        )

    assert service.calls == []


@pytest.mark.asyncio
async def test_manage_admin_user_update_validates_and_updates_body():
    service = FakeAdminService(users__update={"id": "u1", "orgUnitPath": "/Ops"})

    result = await _unwrap(admin_tools.manage_admin_user)(
        service=service,
        user_google_email=EMAIL,
        action="update",
        user_key="u1",
        user={"orgUnitPath": "/Ops"},
    )

    assert result == {"id": "u1", "orgUnitPath": "/Ops"}
    assert service.calls == [
        ("users.update", {"userKey": "u1", "body": {"orgUnitPath": "/Ops"}})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "suspended"), [("suspend", True), ("unsuspend", False)]
)
async def test_manage_admin_user_toggles_suspension(action, suspended):
    service = FakeAdminService(users__update={"id": "u1", "suspended": suspended})

    result = await _unwrap(admin_tools.manage_admin_user)(
        service=service,
        user_google_email=EMAIL,
        action=action,
        user_key="u1",
    )

    assert result["suspended"] is suspended
    assert service.calls == [
        ("users.update", {"userKey": "u1", "body": {"suspended": suspended}})
    ]


@pytest.mark.asyncio
async def test_manage_admin_user_delete_returns_structured_status():
    service = FakeAdminService(users__delete={})

    result = await _unwrap(admin_tools.manage_admin_user)(
        service=service,
        user_google_email=EMAIL,
        action="delete",
        user_key="u1",
    )

    assert result == {"status": "deleted", "userKey": "u1"}
    assert service.calls == [("users.delete", {"userKey": "u1"})]


@pytest.mark.asyncio
async def test_list_admin_groups_preserves_next_page_token():
    assert hasattr(admin_tools, "list_admin_groups")
    service = FakeAdminService(
        groups__list={"groups": [{"id": "g1"}], "nextPageToken": "next-groups"}
    )

    result = await _unwrap(admin_tools.list_admin_groups)(
        service=service,
        user_google_email=EMAIL,
        domain="example.com",
        max_results=25,
        page_token="current-groups",
    )

    assert result == {"groups": [{"id": "g1"}], "nextPageToken": "next-groups"}
    assert service.calls == [
        (
            "groups.list",
            {
                "domain": "example.com",
                "maxResults": 25,
                "pageToken": "current-groups",
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_admin_group_returns_directory_resource():
    assert hasattr(admin_tools, "get_admin_group")
    service = FakeAdminService(
        groups__get={"id": "g1", "email": "team@example.com"}
    )

    result = await _unwrap(admin_tools.get_admin_group)(
        service=service,
        user_google_email=EMAIL,
        group_key="team@example.com",
    )

    assert result == {"id": "g1", "email": "team@example.com"}
    assert service.calls == [("groups.get", {"groupKey": "team@example.com"})]


@pytest.mark.asyncio
async def test_manage_admin_group_create_validates_typed_body():
    assert hasattr(admin_tools, "manage_admin_group")
    service = FakeAdminService(
        groups__insert={"id": "g1", "email": "team@example.com"}
    )

    result = await _unwrap(admin_tools.manage_admin_group)(
        service=service,
        user_google_email=EMAIL,
        action="create",
        group={
            "email": "team@example.com",
            "name": "Team",
            "description": "Operations team",
        },
    )

    assert result == {"id": "g1", "email": "team@example.com"}
    assert service.calls == [
        (
            "groups.insert",
            {
                "body": {
                    "email": "team@example.com",
                    "name": "Team",
                    "description": "Operations team",
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_group_update_requires_key_and_body():
    service = FakeAdminService(groups__update={"id": "g1", "name": "New Team"})

    result = await _unwrap(admin_tools.manage_admin_group)(
        service=service,
        user_google_email=EMAIL,
        action="update",
        group_key="g1",
        group={"name": "New Team"},
    )

    assert result == {"id": "g1", "name": "New Team"}
    assert service.calls == [
        ("groups.update", {"groupKey": "g1", "body": {"name": "New Team"}})
    ]


@pytest.mark.asyncio
async def test_manage_admin_group_delete_returns_structured_status():
    service = FakeAdminService(groups__delete={})

    result = await _unwrap(admin_tools.manage_admin_group)(
        service=service,
        user_google_email=EMAIL,
        action="delete",
        group_key="g1",
    )

    assert result == {"status": "deleted", "groupKey": "g1"}
    assert service.calls == [("groups.delete", {"groupKey": "g1"})]


@pytest.mark.asyncio
async def test_list_admin_group_members_preserves_next_page_token():
    assert hasattr(admin_tools, "list_admin_group_members")
    service = FakeAdminService(
        members__list={"members": [{"id": "m1"}], "nextPageToken": "next-members"}
    )

    result = await _unwrap(admin_tools.list_admin_group_members)(
        service=service,
        user_google_email=EMAIL,
        group_key="g1",
        roles="MEMBER,MANAGER",
        max_results=100,
        page_token="current-members",
    )

    assert result == {
        "members": [{"id": "m1"}],
        "nextPageToken": "next-members",
    }
    assert service.calls == [
        (
            "members.list",
            {
                "groupKey": "g1",
                "roles": "MEMBER,MANAGER",
                "maxResults": 100,
                "pageToken": "current-members",
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_group_membership_add_validates_member():
    assert hasattr(admin_tools, "manage_admin_group_membership")
    service = FakeAdminService(
        members__insert={
            "id": "m1",
            "email": "member@example.com",
            "role": "MEMBER",
        }
    )

    result = await _unwrap(admin_tools.manage_admin_group_membership)(
        service=service,
        user_google_email=EMAIL,
        action="add",
        group_key="g1",
        member_email="member@example.com",
        role="MEMBER",
    )

    assert result["id"] == "m1"
    assert service.calls == [
        (
            "members.insert",
            {
                "groupKey": "g1",
                "body": {"email": "member@example.com", "role": "MEMBER"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_group_membership_updates_role():
    service = FakeAdminService(members__update={"id": "m1", "role": "MANAGER"})

    result = await _unwrap(admin_tools.manage_admin_group_membership)(
        service=service,
        user_google_email=EMAIL,
        action="update_role",
        group_key="g1",
        member_key="m1",
        role="MANAGER",
    )

    assert result == {"id": "m1", "role": "MANAGER"}
    assert service.calls == [
        (
            "members.update",
            {"groupKey": "g1", "memberKey": "m1", "body": {"role": "MANAGER"}},
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_group_membership_remove_returns_status():
    service = FakeAdminService(members__delete={})

    result = await _unwrap(admin_tools.manage_admin_group_membership)(
        service=service,
        user_google_email=EMAIL,
        action="remove",
        group_key="g1",
        member_key="m1",
    )

    assert result == {
        "status": "removed",
        "groupKey": "g1",
        "memberKey": "m1",
    }
    assert service.calls == [
        ("members.delete", {"groupKey": "g1", "memberKey": "m1"})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_type", "operation", "key_name"),
    [("user", "users.aliases.insert", "userKey"), ("group", "groups.aliases.insert", "groupKey")],
)
async def test_manage_admin_alias_add_routes_to_target_resource(
    target_type, operation, key_name
):
    assert hasattr(admin_tools, "manage_admin_alias")
    service = FakeAdminService(
        **{operation.replace(".", "__"): {"alias": "alias@example.com"}}
    )

    result = await _unwrap(admin_tools.manage_admin_alias)(
        service=service,
        user_google_email=EMAIL,
        action="add",
        target_type=target_type,
        target_key="target@example.com",
        alias="alias@example.com",
    )

    assert result == {"alias": "alias@example.com"}
    assert service.calls == [
        (
            operation,
            {
                key_name: "target@example.com",
                "body": {"alias": "alias@example.com"},
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_type", "operation", "key_name"),
    [
        ("user", "users.aliases.delete", "userKey"),
        ("group", "groups.aliases.delete", "groupKey"),
    ],
)
async def test_manage_admin_alias_remove_routes_to_target_resource(
    target_type, operation, key_name
):
    service = FakeAdminService(**{operation.replace(".", "__"): {}})

    result = await _unwrap(admin_tools.manage_admin_alias)(
        service=service,
        user_google_email=EMAIL,
        action="remove",
        target_type=target_type,
        target_key="target@example.com",
        alias="alias@example.com",
    )

    assert result == {
        "status": "removed",
        "targetType": target_type,
        "targetKey": "target@example.com",
        "alias": "alias@example.com",
    }
    assert service.calls == [
        (
            operation,
            {key_name: "target@example.com", "alias": "alias@example.com"},
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_alias_validates_remove_key_before_api_call():
    service = FakeAdminService()

    with pytest.raises(UserInputError, match="target_key is required for remove"):
        await _unwrap(admin_tools.manage_admin_alias)(
            service=service,
            user_google_email=EMAIL,
            action="remove",
            target_type="user",
            target_key="",
            alias="alias@example.com",
        )

    assert service.calls == []


@pytest.mark.asyncio
async def test_list_admin_org_units_returns_structured_units():
    assert hasattr(admin_tools, "list_admin_org_units")
    service = FakeAdminService(
        orgunits__list={"organizationUnits": [{"orgUnitPath": "/Ops"}]}
    )

    result = await _unwrap(admin_tools.list_admin_org_units)(
        service=service,
        user_google_email=EMAIL,
        customer_id="my_customer",
        org_unit_path="/Parent",
        org_unit_type="children",
    )

    assert result == {"organizationUnits": [{"orgUnitPath": "/Ops"}]}
    assert service.calls == [
        (
            "orgunits.list",
            {
                "customerId": "my_customer",
                "orgUnitPath": "/Parent",
                "type": "children",
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_org_unit_create_validates_typed_body():
    assert hasattr(admin_tools, "manage_admin_org_unit")
    service = FakeAdminService(
        orgunits__insert={"orgUnitId": "ou1", "orgUnitPath": "/Ops"}
    )

    result = await _unwrap(admin_tools.manage_admin_org_unit)(
        service=service,
        user_google_email=EMAIL,
        action="create",
        customer_id="my_customer",
        org_unit={"name": "Ops", "parentOrgUnitPath": "/"},
    )

    assert result == {"orgUnitId": "ou1", "orgUnitPath": "/Ops"}
    assert service.calls == [
        (
            "orgunits.insert",
            {
                "customerId": "my_customer",
                "body": {"name": "Ops", "parentOrgUnitPath": "/"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_org_unit_update_requires_path_and_body():
    service = FakeAdminService(
        orgunits__update={"orgUnitId": "ou1", "description": "Updated"}
    )

    result = await _unwrap(admin_tools.manage_admin_org_unit)(
        service=service,
        user_google_email=EMAIL,
        action="update",
        customer_id="my_customer",
        org_unit_path="/Ops",
        org_unit={"description": "Updated"},
    )

    assert result == {"orgUnitId": "ou1", "description": "Updated"}
    assert service.calls == [
        (
            "orgunits.update",
            {
                "customerId": "my_customer",
                "orgUnitPath": "/Ops",
                "body": {"description": "Updated"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_manage_admin_org_unit_delete_returns_structured_status():
    service = FakeAdminService(orgunits__delete={})

    result = await _unwrap(admin_tools.manage_admin_org_unit)(
        service=service,
        user_google_email=EMAIL,
        action="delete",
        customer_id="my_customer",
        org_unit_path="/Ops",
    )

    assert result == {"status": "deleted", "orgUnitPath": "/Ops"}
    assert service.calls == [
        (
            "orgunits.delete",
            {"customerId": "my_customer", "orgUnitPath": "/Ops"},
        )
    ]
