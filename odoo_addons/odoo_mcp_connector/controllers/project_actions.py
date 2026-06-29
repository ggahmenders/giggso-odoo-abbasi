from __future__ import annotations

from typing import Any

from odoo.exceptions import AccessError
from odoo.http import request

from .utils import compact_records


PROJECT_FIELDS = ["id", "name", "user_id", "partner_id", "company_id"]
TASK_DETAIL_FIELDS = [
    "id", "name", "description",
    "project_id", "stage_id", "user_ids",
    "partner_id", "company_id", "tag_ids",
    "date_deadline", "priority", "state", "activity_state",
    "create_date", "write_date",
]
TASK_FIELDS = [
    "id",
    "name",
    "project_id",
    "stage_id",
    "user_ids",
    "partner_id",
    "date_deadline",
    "priority",
    "state",
    "activity_state",
]


def list_projects(user, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List projects visible to the mapped Odoo user."""
    domain = [("name", "ilike", params["query"])] if params.get("query") else []
    records = request.env["project.project"].with_user(user).search_read(
        domain,
        PROJECT_FIELDS,
        limit=int(params.get("limit", 20)),
        order="write_date desc",
    )
    return compact_records(records)


def list_tasks(user, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List project tasks visible to the mapped Odoo user."""
    domain: list[Any] = []
    if params.get("project_id"):
        domain.append(("project_id", "=", int(params["project_id"])))
    if params.get("query"):
        domain.append(("name", "ilike", params["query"]))
    records = request.env["project.task"].with_user(user).search_read(
        domain,
        TASK_FIELDS,
        limit=int(params.get("limit", 30)),
        order="write_date desc",
    )
    return compact_records(records)



def list_task_stages(user, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List project task stages visible to the mapped Odoo user."""
    domain: list[Any] = []
    if params.get("project_id"):
        domain = ["|", ("project_ids", "=", False), ("project_ids", "in", [int(params["project_id"])])]
    records = request.env["project.task.type"].with_user(user).search_read(
        domain,
        ["id", "name", "sequence", "fold", "project_ids"],
        limit=int(params.get("limit", 50)),
        order="sequence asc",
    )
    return compact_records(records)


def create_task(user, params: dict[str, Any]) -> dict[str, Any]:
    """Create a project task as the mapped Odoo user.

    user_ids is set explicitly before create() to bypass Odoo's default_get,
    which resolves self.env.user from the request context. On auth="public"
    routes request.env.user is the Public user (id=3); letting default_get
    fill user_ids would set the task owner to Public, which then fails the
    res.users read-access check and returns 400.
    """
    vals = dict(params["values"])
    assignee_email: str = str(params.get("assignee_email") or "").strip()
    if assignee_email:
        assignee = request.env["res.users"].sudo().search(
            [
                ("active", "=", True),
                "|",
                ("login", "=", assignee_email),
                ("email", "=", assignee_email),
            ],
            limit=1,
        )
        if not assignee:
            raise ValueError(f"No active Odoo user found for assignee: {assignee_email}")
        vals["user_ids"] = [(4, assignee.id)]
    elif "user_ids" not in vals:
        # Default to the authenticated actor — never let default_get pick Public.
        vals["user_ids"] = [(4, user.id)]

    tag_names: list[str] = [n.strip() for n in (params.get("tag_names") or []) if n and n.strip()]
    if tag_names:
        Tag = request.env["project.tags"].with_user(user)
        tag_ids = []
        for tag_name in tag_names:
            tag = Tag.search([("name", "=ilike", tag_name)], limit=1)
            if not tag:
                # Creation is gated on project manager group; read-only members
                # can still use existing tags but cannot introduce new ones.
                if not user.has_group("project.group_project_manager"):
                    raise ValueError(
                        f"Tag '{tag_name}' does not exist and you do not have permission to create tags. "
                        "Ask a project manager to create it first."
                    )
                tag = Tag.create({"name": tag_name})
            tag_ids.append(tag.id)
        vals["tag_ids"] = [(6, 0, tag_ids)]

    task = request.env["project.task"].with_user(user).create(vals)
    return {"id": task.id, "name": task.name, "message": "Project task created"}


def move_task_stage(user, params: dict[str, Any]) -> dict[str, Any]:
    """Move a visible project task to another stage."""
    task = request.env["project.task"].with_user(user).browse(int(params["task_id"])).exists()
    if not task:
        raise ValueError("Project task not found or not visible")
    task.write({"stage_id": int(params["stage_id"])})
    return {"id": task.id, "message": "Project task stage updated"}


def add_comment(user, params: dict[str, Any]) -> dict[str, Any]:
    """Add a chatter comment to a visible project task."""
    task = request.env["project.task"].with_user(user).browse(int(params["task_id"])).exists()
    if not task:
        raise ValueError("Project task not found or not visible")
    task.message_post(body=params["comment"], message_type="comment", subtype_xmlid="mail.mt_comment")
    return {"id": task.id, "message": "Project task comment added"}


def update_task(user, params: dict[str, Any]) -> dict[str, Any]:
    """Update fields on a visible project task including assignees and deadline."""
    task = request.env["project.task"].with_user(user).browse(int(params["task_id"])).exists()
    if not task:
        raise ValueError("Project task not found or not visible")
    values = dict(params.get("values") or {})
    if not values:
        raise ValueError("No fields to update")
    task.write(values)
    return {"id": task.id, "message": "Project task updated"}


def delete_task(user, params: dict[str, Any]) -> dict[str, Any]:
    """Delete a project task. This action is permanent."""
    task = request.env["project.task"].with_user(user).browse(int(params["task_id"])).exists()
    if not task:
        raise ValueError("Project task not found or not visible")
    task_id = task.id
    task_name = task.name
    task.unlink()
    return {"id": task_id, "name": task_name, "message": "Project task deleted"}


def get_task(user, params: dict[str, Any]) -> dict[str, Any]:
    """Return full details of a single project task including comments and attachments."""
    task = request.env["project.task"].with_user(user).browse(int(params["task_id"])).exists()
    if not task:
        raise ValueError("Project task not found or not visible")

    rows = compact_records(task.read(TASK_DETAIL_FIELDS))
    if not rows:
        raise ValueError("Project task was deleted before it could be read")
    record = rows[0]

    # Enrich many2many assignees with name + email.
    # with_user(user) keeps field-level ACLs in effect; fall back to id+name only
    # if the caller lacks rights to read res.users.email (e.g. portal users).
    if record.get("user_ids"):
        Users = request.env["res.users"].with_user(user).browse(record["user_ids"])
        try:
            assignees = Users.read(["id", "name", "email"])
            record["user_ids"] = [{"id": a["id"], "name": a["name"], "email": a.get("email") or ""} for a in assignees]
        except AccessError:
            assignees = Users.read(["id", "name"])
            record["user_ids"] = [{"id": a["id"], "name": a["name"]} for a in assignees]

    # Enrich tags with names; with_user(user) keeps ACLs consistent with all
    # other enrichments in this function — tags are non-sensitive but there is
    # no reason to bypass the caller's access context.
    if record.get("tag_ids"):
        tags = request.env["project.tags"].with_user(user).browse(record["tag_ids"]).read(["id", "name"])
        record["tag_ids"] = [{"id": t["id"], "name": t["name"]} for t in tags]

    # Chatter comments — public only (exclude internal notes via subtype_id.internal).
    # with_user(user) keeps follower-based ir.rules in effect; .sudo() would leak
    # internal notes to any caller regardless of their access level.
    messages = request.env["mail.message"].with_user(user).search_read(
        [
            ("model", "=", "project.task"),
            ("res_id", "=", task.id),
            ("message_type", "in", ["comment", "email"]),
            ("subtype_id.internal", "=", False),
        ],
        ["id", "author_id", "body", "date", "message_type"],
        order="date desc",
        limit=50,
    )
    record["comments"] = compact_records(messages)

    # Attachments — metadata always; binary content only when explicitly requested.
    # 5 MB per-file cap prevents a single large attachment from blowing the response.
    _MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
    include_content: bool = bool(params.get("include_attachment_content"))
    attachment_fields = ["id", "name", "mimetype", "file_size", "create_date"]
    attachment_domain = [("res_model", "=", "project.task"), ("res_id", "=", task.id)]
    if include_content:
        attachment_domain.append(("file_size", "<=", _MAX_ATTACHMENT_BYTES))
        attachment_fields.append("datas")
    attachments = request.env["ir.attachment"].with_user(user).search_read(
        attachment_domain,
        attachment_fields,
        order="create_date desc",
        limit=20,
    )
    record["attachments"] = compact_records(attachments)

    return record
