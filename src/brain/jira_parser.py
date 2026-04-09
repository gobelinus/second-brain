"""
jira_parser.py — Parse external Jira script JSON output into structured data.

Your external Jira script writes one JSON file per ticket:
  inputs/YYYY-MM-DD/jira-ENG-161845.json

This module reads those files and produces a JiraTicket dataclass
that enricher.py uses to assemble the LLM brief.

No network calls. No credentials. Pure local file parsing.

JSON Structure (from your Jira script)
───────────────────────────────────────
{
  "ticket_key": "ENG-161845",
  "issue_details": {
    "renderedFields": {
      "description": "<html>...</html>",   ← HTML, easier to parse
      "duedate": "03/Apr/26",
      "updated": "30/Mar/26 8:45 PM"
    },
    "fields": {
      "created": "2025-09-23T09:06:33...",
      "updated": "2026-03-30T20:45:03...",
      "customfield_10020": [sprint objects],
      "customfield_10267": [{ value: "Product Area" }],
      "comment": {
        "comments": [
          {
            "id": "...",
            "author": { "displayName": "Alice" },
            "body": "<p>HTML comment body</p>",
            "created": "...",
            "updated": "..."
          }
        ]
      }
    },
    "names": { "customfield_10020": "Sprint", ... },
    "changelog": { "histories": [...] }
  },
  "development_info": [{
    "branches": [{ "lastCommit": { "message": "...", "files": [...] } }],
    "pullRequests": [{
      "id": "#103018",
      "name": "...",
      "status": "DRAFT",
      "reviewers": [...],
      "source": { "branch": "..." },
      "destination": { "branch": "master" }
    }]
  }],
  "comments": { "total": 0, "comments": [] },   ← always empty, ignore
  "subtasks": { "issues": [] },
  "epic_children": { "issues": [] }
}

Key parsing notes
─────────────────
  Status:      Derived from changelog.histories — last entry with field="status"
  Assignee:    Derived from changelog.histories — last entry with field="assignee"
  Comments:    Live in issue_details.fields.comment.comments (HTML body strings)
  Sprint:      issue_details.fields.customfield_10020 — find the active one
  Product Area: issue_details.fields.customfield_10267[0].value
  PR links:    development_info[].pullRequests — cross-reference with dump PRs
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class JiraComment:
    """A single comment on a Jira ticket."""

    comment_id: str
    author: str
    body: str  # plain text, HTML stripped
    created: str  # ISO date string YYYY-MM-DD
    is_system: bool  # True for bot/automation authors


@dataclass
class JiraLinkedPR:
    """A GitHub PR linked to a Jira ticket via development_info."""

    pr_id: str  # e.g. "#103018"
    title: str
    status: str  # "DRAFT" | "OPEN" | "MERGED" | "DECLINED"
    source_branch: str
    target_branch: str
    reviewers: list[str]
    last_updated: str  # ISO date string


@dataclass
class JiraLinkedBranch:
    """A Git branch linked to a Jira ticket."""

    name: str
    last_commit_message: str
    last_commit_author: str
    files_changed: list[str]  # file paths from last commit


@dataclass
class JiraTicket:
    """
    Structured representation of a Jira ticket parsed from external script output.

    All fields default to safe empty values.
    Callers use what they need without guarding every access.
    """

    ticket_key: str

    # Core fields
    summary: str = ""
    status: str = ""  # derived from changelog
    assignee: str = ""  # derived from changelog
    reporter: str = ""
    priority: str = ""
    issue_type: str = ""
    created: str = ""
    updated: str = ""
    due_date: str = ""

    # Content
    description: str = ""  # plain text, HTML stripped

    # Metadata
    sprint: str = ""  # active sprint name
    product_area: str = ""  # e.g. "Core Infrastructure (CI)"
    pod_name: str = ""  # e.g. "CI-Prod Infra"
    labels: list[str] = field(default_factory=list)

    # Comments (from issue_details.fields.comment.comments)
    comments: list[JiraComment] = field(default_factory=list)

    # Development info
    linked_prs: list[JiraLinkedPR] = field(default_factory=list)
    linked_branches: list[JiraLinkedBranch] = field(default_factory=list)

    # Subtasks and epic children
    subtask_keys: list[str] = field(default_factory=list)
    epic_children_keys: list[str] = field(default_factory=list)

    def to_context_block(self) -> str:
        """
        Render ticket as a clean markdown context block for LLM consumption.
        Used by enricher.py when assembling the task brief prompt.
        """
        lines = [
            f"## Jira: {self.ticket_key} — {self.summary}",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Status | {self.status or '—'} |",
            f"| Assignee | {self.assignee or '—'} |",
            f"| Priority | {self.priority or '—'} |",
            f"| Type | {self.issue_type or '—'} |",
            f"| Sprint | {self.sprint or '—'} |",
            f"| Product Area | {self.product_area or '—'} |",
            f"| POD | {self.pod_name or '—'} |",
            f"| Due Date | {self.due_date or '—'} |",
            f"| Created | {self.created or '—'} |",
            f"| Updated | {self.updated or '—'} |",
        ]

        if self.labels:
            lines.append(f"| Labels | {', '.join(self.labels)} |")

        # Description
        if self.description:
            desc = self.description[:600]
            if len(self.description) > 600:
                desc += "\n_(truncated — see ticket for full description)_"
            lines.append(f"\n### Description\n\n{desc}")

        # Comments
        if self.comments:
            lines.append(f"\n### Comments ({len(self.comments)})\n")
            for c in self.comments:
                if c.is_system:
                    continue  # skip bot comments in context block
                lines.append(f"**{c.author}** ({c.created}):")
                lines.append(f"{c.body[:300]}")
                lines.append("")

        # Linked PRs
        if self.linked_prs:
            lines.append("\n### Linked Pull Requests\n")
            for pr in self.linked_prs:
                reviewers_str = ", ".join(pr.reviewers) if pr.reviewers else "none"
                lines.append(
                    f"- {pr.pr_id} — {pr.title} "
                    f"[{pr.status}] "
                    f"`{pr.source_branch}` → `{pr.target_branch}` "
                    f"| Reviewers: {reviewers_str}"
                )

        # Linked branches (last commit summary)
        if self.linked_branches:
            lines.append("\n### Recent Branch Activity\n")
            for br in self.linked_branches:
                lines.append(f"- `{br.name}` — {br.last_commit_message[:80]}")
                if br.files_changed:
                    lines.append(
                        f"  Files: {', '.join(br.files_changed[:5])}"
                        + ("..." if len(br.files_changed) > 5 else "")
                    )

        return "\n".join(lines)


# ── Parser ────────────────────────────────────────────────────────────────────


class JiraParser:
    """
    Parses the JSON output from your external Jira script into JiraTicket objects.

    Handles:
      - HTML body strings (comments, rendered description)
      - ADF document format (fields.description)
      - Changelog-derived status and assignee
      - Sprint from customfield_10020
      - Product Area from customfield_10267
      - Development info (linked PRs and branches)

    Fails gracefully — any missing field returns empty/default value.
    """

    # Known bot/automation account patterns — skip these in comment summaries
    _SYSTEM_ACCOUNT_PATTERNS = [
        r"automation",
        r"jira-api-user",
        r"bot",
        r"service-account",
        r"jenkins",
        r"ci-user",
    ]

    def parse_file(self, json_path: str | Path) -> JiraTicket | None:
        """
        Parse a single Jira JSON file into a JiraTicket.

        Args:
            json_path: Path (str or Path) to jira-TICKET-ID.json file

        Returns:
            JiraTicket on success, None on parse failure.
        """
        json_path = Path(json_path) if isinstance(json_path, str) else json_path

        if not json_path.exists():
            return None

        try:
            content = json_path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(content, strict=False)
        except json.JSONDecodeError as e:
            print(f"        ⚠️  JSON parse error in {json_path.name}: {e}")
            return self._parse_fallback(json_path.stem, content)
        except Exception as e:
            print(f"        ⚠️  Could not read {json_path.name}: {e}")
            return None

        return self._parse_data(data)

    def parse_json(self, data: dict) -> JiraTicket:
        """Parse an already-loaded JSON dict into a JiraTicket."""
        return self._parse_data(data)

    # ── Core Parsing ──────────────────────────────────────────────────────────

    def _parse_data(self, data: dict) -> JiraTicket:
        """
        Map raw JSON dict to JiraTicket.

        Navigates the nested structure:
          data.ticket_key
          data.issue_details.renderedFields  ← HTML strings
          data.issue_details.fields          ← structured values
          data.issue_details.names           ← customfield name map
          data.issue_details.changelog       ← status/assignee history
          data.development_info              ← linked PRs and branches
        """
        ticket_key = data.get("ticket_key", "UNKNOWN")
        issue = data.get("issue_details", {})
        rendered = issue.get("renderedFields", {})
        fields = issue.get("fields", {})
        # names = issue.get("names", {})
        changelog = issue.get("changelog", {})

        # ── Status and Assignee from changelog ────────────────────────────────
        status, assignee = self._derive_from_changelog(changelog)

        # ── Summary ───────────────────────────────────────────────────────────
        summary = fields.get("summary") or rendered.get("summary") or ""

        # ── Description ───────────────────────────────────────────────────────
        # Prefer renderedFields.description (HTML) — cleaner than ADF parsing
        desc_html = rendered.get("description", "")
        desc_adf = fields.get("description")
        if desc_html:
            description = _strip_html(desc_html)
        elif isinstance(desc_adf, dict):
            description = _extract_adf_text(desc_adf)
        elif isinstance(desc_adf, str):
            description = _strip_html(desc_adf)
        else:
            description = ""

        # ── Dates ─────────────────────────────────────────────────────────────
        created = _parse_jira_date(fields.get("created") or rendered.get("created", ""))
        updated = _parse_jira_date(fields.get("updated") or rendered.get("updated", ""))
        due_date = (
            rendered.get("duedate")  # "03/Apr/26" — already readable
            or fields.get("duedate", "")
        )

        # ── Sprint (customfield_10020) ────────────────────────────────────────
        sprint = self._extract_sprint(fields)

        # ── Product Area (customfield_10267) ──────────────────────────────────
        product_area = self._extract_select_value(fields.get("customfield_10267"))

        # ── POD Name (customfield_10219 — from changelog) ─────────────────────
        pod_name = self._extract_changelog_field(changelog, "POD Name")

        # ── Priority ──────────────────────────────────────────────────────────
        priority_obj = fields.get("priority") or {}
        priority = (
            priority_obj.get("name", "") if isinstance(priority_obj, dict) else ""
        )

        # ── Issue type ────────────────────────────────────────────────────────
        issuetype_obj = fields.get("issuetype") or {}
        issue_type = (
            issuetype_obj.get("name", "") if isinstance(issuetype_obj, dict) else ""
        )

        # ── Reporter ──────────────────────────────────────────────────────────
        reporter_obj = fields.get("reporter") or {}
        reporter = (
            reporter_obj.get("displayName", "")
            if isinstance(reporter_obj, dict)
            else ""
        )

        # ── Labels ────────────────────────────────────────────────────────────
        raw_labels = fields.get("labels") or []
        labels = [line for line in raw_labels if isinstance(line, str)]

        # ── Comments (from fields.comment.comments) ───────────────────────────
        comment_block = fields.get("comment") or {}
        raw_comments = comment_block.get("comments", [])
        comments = [self._parse_comment(c) for c in raw_comments]

        # ── Development info (linked PRs + branches) ──────────────────────────
        dev_info = data.get("development_info", [])
        linked_prs: list[JiraLinkedPR] = []
        linked_branches: list[JiraLinkedBranch] = []

        for dev_entry in dev_info:
            for pr_raw in dev_entry.get("pullRequests", []):
                pr = self._parse_linked_pr(pr_raw)
                if pr:
                    linked_prs.append(pr)
            for branch_raw in dev_entry.get("branches", []):
                branch = self._parse_linked_branch(branch_raw)
                if branch:
                    linked_branches.append(branch)

        # ── Subtasks and epic children ────────────────────────────────────────
        subtask_keys = [
            s.get("key", "") for s in (data.get("subtasks", {}).get("issues") or [])
        ]
        epic_children_keys = [
            s.get("key", "")
            for s in (data.get("epic_children", {}).get("issues") or [])
        ]

        return JiraTicket(
            ticket_key=ticket_key,
            summary=summary.strip(),
            status=status,
            assignee=assignee,
            reporter=reporter,
            priority=priority,
            issue_type=issue_type,
            created=created,
            updated=updated,
            due_date=due_date.strip(),
            description=description.strip(),
            sprint=sprint,
            product_area=product_area,
            pod_name=pod_name,
            labels=labels,
            comments=comments,
            linked_prs=linked_prs,
            linked_branches=linked_branches,
            subtask_keys=[k for k in subtask_keys if k],
            epic_children_keys=[k for k in epic_children_keys if k],
        )

    # ── Field Extractors ──────────────────────────────────────────────────────

    def _derive_from_changelog(
        self,
        changelog: dict,
    ) -> tuple[str, str]:
        """
        Extract current status and assignee from changelog histories.

        The fields.status and fields.assignee are sometimes null in the
        script output. The changelog is the authoritative source — the last
        entry for each field is the current value.

        Returns: (status, assignee)
        """
        histories = changelog.get("histories", [])

        status = ""
        assignee = ""

        # Walk forward — later entries override earlier ones
        for history in histories:
            for item in history.get("items", []):
                field_name = item.get("field", "")
                to_value = item.get("toString", "") or ""

                if field_name == "status" and to_value:
                    status = to_value

                if field_name == "assignee" and to_value:
                    assignee = to_value

        return status, assignee

    def _extract_changelog_field(
        self,
        changelog: dict,
        field_name: str,
    ) -> str:
        """Extract the latest value of a named field from changelog."""
        histories = changelog.get("histories", [])
        value = ""
        for history in histories:
            for item in history.get("items", []):
                if item.get("field") == field_name:
                    to_val = item.get("toString", "") or ""
                    if to_val:
                        value = to_val
        return value

    def _extract_sprint(self, fields: dict) -> str:
        """
        Extract active sprint name from customfield_10020.

        The field is a list of sprint objects — find the one with state="active".
        Fall back to the most recent closed sprint if no active one found.
        """
        sprints = fields.get("customfield_10020") or []
        if not isinstance(sprints, list):
            return ""

        # Prefer active sprint
        for sprint in sprints:
            if isinstance(sprint, dict) and sprint.get("state") == "active":
                return sprint.get("name", "")

        # Fall back to most recently ended sprint
        closed = [
            s for s in sprints if isinstance(s, dict) and s.get("state") == "closed"
        ]
        if closed:
            # Sort by endDate descending
            try:
                closed.sort(key=lambda s: s.get("endDate", ""), reverse=True)
                return f"{closed[0].get('name', '')} (closed)"
            except Exception:
                return closed[-1].get("name", "")

        return ""

    def _extract_select_value(self, field_value) -> str:
        """
        Extract value from a Jira select/option field.

        Handles both:
          [{"value": "Core Infrastructure (CI)"}]   ← array of options
          {"value": "Implementation Issue"}           ← single option
        """
        if not field_value:
            return ""
        if isinstance(field_value, list):
            values = [v.get("value", "") for v in field_value if isinstance(v, dict)]
            return ", ".join(v for v in values if v)
        if isinstance(field_value, dict):
            return field_value.get("value", "")
        return str(field_value)

    def _parse_comment(self, raw: dict) -> JiraComment:
        """Parse a single comment dict from fields.comment.comments."""
        author_obj = raw.get("author") or {}
        author = author_obj.get("displayName", "") or author_obj.get("emailAddress", "")

        body_raw = raw.get("body", "")
        if isinstance(body_raw, str):
            body = _strip_html(body_raw)
        elif isinstance(body_raw, dict):
            body = _extract_adf_text(body_raw)
        else:
            body = str(body_raw)

        created = _parse_jira_date(raw.get("created", ""))

        # Detect system/bot authors
        author_lower = author.lower()
        email = (author_obj.get("emailAddress") or "").lower()
        is_system = any(
            re.search(pattern, author_lower) or re.search(pattern, email)
            for pattern in self._SYSTEM_ACCOUNT_PATTERNS
        )

        return JiraComment(
            comment_id=raw.get("id", ""),
            author=author,
            body=body.strip(),
            created=created,
            is_system=is_system,
        )

    def _parse_linked_pr(self, raw: dict) -> JiraLinkedPR | None:
        """Parse a PR from development_info[].pullRequests."""
        pr_id = raw.get("id", "")
        if not pr_id:
            return None

        reviewers = [
            r.get("name", "")
            for r in raw.get("reviewers", [])
            if isinstance(r, dict) and r.get("name")
        ]

        source = raw.get("source") or {}
        destination = raw.get("destination") or {}

        return JiraLinkedPR(
            pr_id=pr_id,
            title=raw.get("name", ""),
            status=raw.get("status", ""),
            source_branch=source.get("branch", ""),
            target_branch=destination.get("branch", ""),
            reviewers=reviewers,
            last_updated=_parse_jira_date(raw.get("lastUpdate", "")),
        )

    def _parse_linked_branch(self, raw: dict) -> JiraLinkedBranch | None:
        """Parse a branch from development_info[].branches."""
        name = raw.get("name", "")
        if not name:
            return None

        last_commit = raw.get("lastCommit") or {}
        commit_message = last_commit.get("message", "").split("\n")[
            0
        ]  # first line only
        author_obj = last_commit.get("author") or {}
        commit_author = author_obj.get("name", "")

        files = [
            f.get("path", "")
            for f in last_commit.get("files", [])
            if isinstance(f, dict) and f.get("path")
        ]

        return JiraLinkedBranch(
            name=name,
            last_commit_message=commit_message,
            last_commit_author=commit_author,
            files_changed=files,
        )

    def _parse_fallback(self, stem: str, content: str) -> JiraTicket | None:
        """
        Minimal fallback when JSON is unparseable.
        Extracts ticket key and summary via regex — better than returning None.
        """
        ticket_key = stem.replace("jira-", "").upper()
        summary_match = re.search(r'"summary"\s*:\s*"([^"]{1,200})"', content)
        summary = summary_match.group(1) if summary_match else ""

        if not summary:
            return None

        return JiraTicket(
            ticket_key=ticket_key,
            summary=summary,
            description="(JSON parse error — partial data only)",
        )


# ── Module-Level Helpers ──────────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    """
    Strip HTML tags from a string and clean up whitespace.

    Handles:
      <p>, <ul>, <li>, <h1>-<h6>  → newlines
      <br>, <br/>                  → newline
      <strong>, <em>, <code>       → stripped, text preserved
      <a href="...">text</a>       → "text" only
      HTML entities (&amp; &lt;)   → decoded

    Not a full HTML parser — fast and good enough for Jira comment bodies.
    """
    if not html:
        return ""

    # Replace block elements with newlines before stripping
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(
        r"</?(p|div|li|h[1-6]|blockquote|pre)[^>]*>", "\n", html, flags=re.IGNORECASE
    )
    html = re.sub(r"<ul[^>]*>|</ul>|<ol[^>]*>|</ol>", "\n", html, flags=re.IGNORECASE)

    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", "", html)

    # Decode common HTML entities
    html = (
        html.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )

    # Clean up whitespace — collapse multiple newlines/spaces
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)

    return html.strip()


def _extract_adf_text(node: dict, depth: int = 0) -> str:
    """
    Recursively extract plain text from Atlassian Document Format (ADF).
    Same implementation as mcp_client.py — duplicated here so jira_parser.py
    is standalone with no cross-module dependency.
    """
    if depth > 20:
        return ""

    node_type = node.get("type", "")
    parts: list[str] = []

    if node_type == "text":
        return node.get("text", "")

    for child in node.get("content", []):
        if isinstance(child, dict):
            text = _extract_adf_text(child, depth + 1)
            if text:
                parts.append(text)

    separator = (
        "\n"
        if node_type
        in (
            "paragraph",
            "heading",
            "listItem",
            "bulletList",
            "orderedList",
            "blockquote",
            "codeBlock",
            "rule",
        )
        else ""
    )

    return separator.join(parts) + separator


def _parse_jira_date(raw: str) -> str:
    """
    Normalise various Jira date formats to YYYY-MM-DD.

    Handles:
      "2026-03-30T20:45:03.277-0700"  → "2026-03-30"
      "2025-09-23T09:06:33.447-0700"  → "2025-09-23"
      "03/Apr/26"                      → "2026-04-03"
      "30/Mar/26 8:45 PM"             → "2026-03-30"
      ""                               → ""
    """
    if not raw:
        return ""

    # ISO format — most common
    iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_match:
        return iso_match.group(1)

    # Jira rendered date: "03/Apr/26" or "30/Mar/26 8:45 PM"
    rendered_match = re.match(r"(\d{1,2})/(\w{3})/(\d{2})", raw)
    if rendered_match:
        day, month_abbr, year_2 = rendered_match.groups()
        month_map = {
            "Jan": "01",
            "Feb": "02",
            "Mar": "03",
            "Apr": "04",
            "May": "05",
            "Jun": "06",
            "Jul": "07",
            "Aug": "08",
            "Sep": "09",
            "Oct": "10",
            "Nov": "11",
            "Dec": "12",
        }
        month_num = month_map.get(month_abbr, "01")
        year_4 = f"20{year_2}"
        return f"{year_4}-{month_num}-{int(day):02d}"

    return raw[:10] if len(raw) >= 10 else raw
