"""
mcp_client.py — Atlassian MCP client for Brain Phase 5b.

Thin wrapper around Atlassian's MCP server for Jira and Confluence.
Returns structured dicts — callers never need to know MCP protocol details.

Design Principles
─────────────────
  Graceful degradation:
    Every public method returns None on any failure.
    A missing Jira ticket or unreachable Confluence page produces a warning,
    not an exception. The TaskEnricher continues with local brain context only.

  Single server, two tools:
    Atlassian hosts both Jira and Confluence tools on the same MCP endpoint
    (https://mcp.atlassian.com/mcp). The client calls different tool names,
    not different URLs.

  Enabled flags:
    mcp.jira.enabled and mcp.confluence.enabled are checked before any
    network call. Flip either to false in config.local.yaml to go
    local-only on a given machine without touching tracked config.

  No auth in this file:
    Atlassian MCP uses OAuth — credentials are handled at the MCP server
    level (via ~/.mcp/atlassian or environment variables), not here.
    This client only constructs tool call payloads and parses responses.

  Tool names:
    Atlassian MCP exposes these tool names (as of 2026):
      jira_get_issue          — get full ticket by ID
      jira_search             — JQL search
      jira_get_issue_comments — get comments for a ticket
      confluence_get_page     — get page content by ID or URL
      confluence_search       — CQL search

Usage
─────
  client = MCPClient(config)

  # Get Jira ticket
  ticket = client.get_jira_ticket("CS-15011")
  if ticket is None:
      print("Jira unavailable — continuing with local context")

  # Get linked Confluence page
  page = client.get_confluence_page("https://yourorg.atlassian.net/wiki/...")
  if page is None:
      print("Confluence unavailable — skipping")
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from brain.config import BrainConfig

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class JiraTicket:
    """
    Structured representation of a Jira issue.

    All fields default to safe empty values — callers check what they need
    without guarding every field access.
    """

    ticket_id: str
    summary: str = ""
    status: str = ""
    assignee: str = ""
    reporter: str = ""
    priority: str = ""
    issue_type: str = ""
    created: str = ""
    updated: str = ""
    description: str = ""
    comments: list[str] = field(default_factory=list)  # last N comment bodies
    linked_issues: list[str] = field(
        default_factory=list
    )  # e.g. ["CS-100 (blocks)", "CS-200 (is blocked by)"]
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    url: str = ""

    def to_context_block(self) -> str:
        """
        Render ticket as a clean markdown context block for LLM consumption.
        Used by TaskEnricher when assembling the brief prompt.
        """
        lines = [
            f"## Jira: {self.ticket_id} — {self.summary}",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Status | {self.status or '—'} |",
            f"| Priority | {self.priority or '—'} |",
            f"| Type | {self.issue_type or '—'} |",
            f"| Assignee | {self.assignee or '—'} |",
            f"| Reporter | {self.reporter or '—'} |",
            f"| Created | {self.created or '—'} |",
            f"| Updated | {self.updated or '—'} |",
        ]

        if self.components:
            lines.append(f"| Components | {', '.join(self.components)} |")

        if self.labels:
            lines.append(f"| Labels | {', '.join(self.labels)} |")

        if self.url:
            lines.append(f"| URL | {self.url} |")

        if self.description:
            lines.append(f"\n### Description\n\n{self.description[:500]}")
            if len(self.description) > 500:
                lines.append("_(truncated — see ticket for full description)_")

        if self.linked_issues:
            lines.append("\n### Linked Issues\n")
            for link in self.linked_issues:
                lines.append(f"- {link}")

        if self.comments:
            lines.append(f"\n### Recent Comments ({len(self.comments)})\n")
            for i, comment in enumerate(self.comments, 1):
                # Truncate long comments — we want signal not noise
                preview = comment[:300]
                if len(comment) > 300:
                    preview += "..."
                lines.append(f"**Comment {i}:**\n{preview}\n")

        return "\n".join(lines)


@dataclass
class ConfluencePage:
    """
    Structured representation of a Confluence page.

    Content is truncated to a reasonable length for LLM context.
    Full page content would overflow the context window.
    """

    page_id: str
    title: str = ""
    space: str = ""
    url: str = ""
    content: str = ""  # truncated plain text content
    last_modified: str = ""
    author: str = ""

    # Max characters of page content to include in context
    CONTENT_LIMIT: int = 1500

    def to_context_block(self) -> str:
        """
        Render page as a clean markdown context block for LLM consumption.
        """
        content_preview = self.content[: self.CONTENT_LIMIT]
        if len(self.content) > self.CONTENT_LIMIT:
            content_preview += "\n_(truncated — see page for full content)_"

        lines = [
            f"## Confluence: {self.title}",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Space | {self.space or '—'} |",
            f"| Author | {self.author or '—'} |",
            f"| Last Modified | {self.last_modified or '—'} |",
        ]

        if self.url:
            lines.append(f"| URL | {self.url} |")

        if content_preview:
            lines.append(f"\n### Content\n\n{content_preview}")

        return "\n".join(lines)


@dataclass
class MCPCallResult:
    """
    Raw result from a single MCP tool call.
    Used internally — callers receive structured JiraTicket / ConfluencePage.
    """

    success: bool
    tool_name: str
    data: dict | None = None
    error: str = ""
    raw_response: str = ""


# ── MCP Client ────────────────────────────────────────────────────────────────


class MCPClient:
    """
    Thin wrapper around Atlassian's MCP server for Jira and Confluence.

    All public methods return None on any failure — callers implement
    graceful degradation, not error handling.

    Authentication is handled at the MCP server level (OAuth via Atlassian).
    This client only constructs tool payloads and parses structured responses.
    """

    # Maximum comments to fetch per ticket — keeps context focused
    MAX_COMMENTS = 5

    def __init__(self, config: BrainConfig | None = None):
        self.config = config or BrainConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_jira_ticket(self, ticket_id: str) -> JiraTicket | None:
        """
        Fetch full ticket details for a Jira issue ID.

        Fetches: summary, status, assignee, priority, description,
                 last N comments, linked issues, labels, components.

        Args:
            ticket_id: Jira issue key e.g. "CS-15011", "ENG-42"

        Returns:
            JiraTicket with all available fields populated.
            None if: Jira MCP disabled, ticket not found, any network error.
        """
        if not self.config.mcp_jira_enabled:
            print(f"        ℹ️  Jira MCP disabled — skipping {ticket_id}")
            return None

        print(f"        🔍 Fetching Jira ticket: {ticket_id}...")

        # ── Fetch main ticket ─────────────────────────────────────────────────
        result = self._call_tool(
            server_url=self.config.mcp_jira_server_url,
            tool_name="jira_get_issue",
            arguments={
                "issue_key": ticket_id,
                # Request expanded fields for richer context
                "expand": ["renderedFields", "names", "changelog"],
            },
        )

        if not result.success or not result.data:
            print(f"        ⚠️  Could not fetch {ticket_id}: {result.error}")
            return None

        ticket = self._parse_jira_issue(ticket_id, result.data)

        # ── Fetch comments separately ─────────────────────────────────────────
        # Atlassian MCP returns comments via a dedicated tool call
        comments_result = self._call_tool(
            server_url=self.config.mcp_jira_server_url,
            tool_name="jira_get_issue_comments",
            arguments={
                "issue_key": ticket_id,
                "limit": self.MAX_COMMENTS,
                "order_by": "created",
                "order_direction": "DESC",  # most recent first
            },
        )

        if comments_result.success and comments_result.data:
            ticket.comments = self._parse_jira_comments(comments_result.data)

        print(f"        ✅ {ticket_id}: {ticket.summary[:60]} [{ticket.status}]")
        return ticket

    def get_confluence_page(self, page_id_or_url: str) -> ConfluencePage | None:
        """
        Fetch a Confluence page by ID or URL.

        Handles both formats:
          - Page ID:  "12345678"
          - Full URL: "https://yourorg.atlassian.net/wiki/spaces/ENG/pages/12345678/..."

        Content is truncated to CONTENT_LIMIT chars — enough for LLM context,
        not enough to blow the prompt window.

        Args:
            page_id_or_url: Confluence page ID or full URL

        Returns:
            ConfluencePage with title + truncated content.
            None if: Confluence MCP disabled, page not found, any error.
        """
        if not self.config.mcp_confluence_enabled:
            print("        ℹ️  Confluence MCP disabled — skipping page fetch")
            return None

        page_id = self._extract_confluence_page_id(page_id_or_url)
        display_ref = page_id or page_id_or_url[:60]
        print(f"        🔍 Fetching Confluence page: {display_ref}...")

        result = self._call_tool(
            server_url=self.config.mcp_confluence_server_url,
            tool_name="confluence_get_page",
            arguments={
                # Pass both — MCP server handles whichever is provided
                "page_id": page_id or "",
                "url": page_id_or_url if not page_id else "",
                "include_content": True,
                "content_format": "plain",  # plain text, not storage format XML
            },
        )

        if not result.success or not result.data:
            print(f"        ⚠️  Could not fetch Confluence page: {result.error}")
            return None

        page = self._parse_confluence_page(result.data, page_id_or_url)
        print(f"        ✅ Confluence: {page.title[:60]}")
        return page

    def search_jira(self, jql: str, max_results: int = 10) -> list[JiraTicket]:
        """
        Run a JQL query and return matching tickets.

        Useful for finding related tickets — e.g. searching for tickets
        in the same project mentioning the same people or components.

        Args:
            jql:         JQL query string e.g. 'project = CS AND assignee = currentUser()'
            max_results: Cap on results returned (default 10)

        Returns:
            List of JiraTicket objects. Empty list on any failure.
        """
        if not self.config.mcp_jira_enabled:
            return []

        result = self._call_tool(
            server_url=self.config.mcp_jira_server_url,
            tool_name="jira_search",
            arguments={
                "jql": jql,
                "max_results": max_results,
                "fields": ["summary", "status", "assignee", "priority", "updated"],
            },
        )

        if not result.success or not result.data:
            return []

        issues = result.data.get("issues", [])
        return [
            self._parse_jira_issue(
                issue.get("key", "UNKNOWN"),
                issue,
            )
            for issue in issues
        ]

    def search_confluence(
        self, query: str, max_results: int = 5
    ) -> list[ConfluencePage]:
        """
        Run a CQL search across Confluence.

        Args:
            query:       CQL query e.g. 'title ~ "Route 53" AND space = "ENG"'
            max_results: Cap on results returned (default 5)

        Returns:
            List of ConfluencePage objects. Empty list on any failure.
        """
        if not self.config.mcp_confluence_enabled:
            return []

        result = self._call_tool(
            server_url=self.config.mcp_confluence_server_url,
            tool_name="confluence_search",
            arguments={
                "query": query,
                "max_results": max_results,
                "include_content": False,  # titles only for search results
            },
        )

        if not result.success or not result.data:
            return []

        pages = result.data.get("pages", [])
        return [
            ConfluencePage(
                page_id=p.get("id", ""),
                title=p.get("title", ""),
                space=p.get("space", {}).get("key", ""),
                url=p.get("url", ""),
            )
            for p in pages
        ]

    def is_jira_available(self) -> bool:
        """
        Quick connectivity check for Jira MCP.

        Uses jira_search with a minimal JQL — cheaper than fetching a full ticket.
        Returns False if disabled, unreachable, or auth fails.
        """
        if not self.config.mcp_jira_enabled:
            return False
        result = self._call_tool(
            server_url=self.config.mcp_jira_server_url,
            tool_name="jira_search",
            arguments={"jql": "order by created DESC", "max_results": 1},
        )
        return result.success

    def is_confluence_available(self) -> bool:
        """
        Quick connectivity check for Confluence MCP.
        Returns False if disabled, unreachable, or auth fails.
        """
        if not self.config.mcp_confluence_enabled:
            return False
        result = self._call_tool(
            server_url=self.config.mcp_confluence_server_url,
            tool_name="confluence_search",
            arguments={"query": "type = page", "max_results": 1},
        )
        return result.success

    # ── MCP Protocol Layer ─────────────────────────────────────────────────────

    def _call_tool(
        self,
        server_url: str,
        tool_name: str,
        arguments: dict,
    ) -> MCPCallResult:
        """
        Make a single MCP tool call via HTTP POST to the MCP server endpoint.

        MCP protocol (simplified):
          POST {server_url}
          Content-Type: application/json

          {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
              "name": "{tool_name}",
              "arguments": {arguments}
            },
            "id": 1
          }

        Response:
          {
            "jsonrpc": "2.0",
            "result": {
              "content": [{"type": "text", "text": "{json_string}"}]
            },
            "id": 1
          }

        The actual tool result lives in result.content[0].text as a
        JSON string — we parse it into a dict and return it.

        Fails gracefully — any exception returns MCPCallResult(success=False).
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": 1,
        }

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url=server_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # MCP auth is handled via bearer token injected by
                    # the MCP server infrastructure (e.g. Atlassian OAuth)
                    # If your setup requires explicit auth, add here:
                    # "Authorization": f"Bearer {token}",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")

            envelope = json.loads(raw)

            # Check for JSON-RPC error
            if "error" in envelope:
                error_msg = envelope["error"].get("message", "Unknown MCP error")
                return MCPCallResult(
                    success=False,
                    tool_name=tool_name,
                    error=error_msg,
                    raw_response=raw,
                )

            # Extract result content
            result_block = envelope.get("result", {})
            content_list = result_block.get("content", [])

            if not content_list:
                return MCPCallResult(
                    success=False,
                    tool_name=tool_name,
                    error="MCP response contained no content",
                    raw_response=raw,
                )

            # Content[0].text contains the JSON string of the tool result
            text_content = next(
                (c.get("text", "") for c in content_list if c.get("type") == "text"),
                "",
            )

            if not text_content:
                return MCPCallResult(
                    success=False,
                    tool_name=tool_name,
                    error="MCP tool returned empty text content",
                    raw_response=raw,
                )

            # Parse the inner JSON
            try:
                data = json.loads(text_content)
            except json.JSONDecodeError:
                # Some tools return plain text, not JSON — wrap it
                data = {"text": text_content}

            return MCPCallResult(
                success=True,
                tool_name=tool_name,
                data=data,
                raw_response=raw,
            )

        except urllib.error.HTTPError as e:
            return MCPCallResult(
                success=False,
                tool_name=tool_name,
                error=f"HTTP {e.code}: {e.reason}",
            )

        except urllib.error.URLError as e:
            return MCPCallResult(
                success=False,
                tool_name=tool_name,
                error=f"Network error: {e.reason}",
            )

        except TimeoutError:
            return MCPCallResult(
                success=False,
                tool_name=tool_name,
                error="MCP server timeout (30s)",
            )

        except Exception as e:
            return MCPCallResult(
                success=False,
                tool_name=tool_name,
                error=f"Unexpected error: {type(e).__name__}: {e}",
            )

    # ── Jira Response Parsers ──────────────────────────────────────────────────

    def _parse_jira_issue(self, ticket_id: str, data: dict) -> JiraTicket:
        """
        Parse raw Jira MCP response into a JiraTicket.

        Atlassian MCP returns fields nested under data["fields"].
        Handles missing fields gracefully — every access uses .get().
        """
        fields = data.get("fields", data)  # some responses flatten fields

        # Assignee and reporter can be None (unassigned tickets)
        assignee_obj = fields.get("assignee") or {}
        reporter_obj = fields.get("reporter") or {}
        priority_obj = fields.get("priority") or {}
        status_obj = fields.get("status") or {}
        issuetype_obj = fields.get("issuetype") or {}

        # Extract linked issue summaries
        linked: list[str] = []
        for link in fields.get("issuelinks", []):
            link_type = link.get("type", {}).get("name", "")
            if "inwardIssue" in link:
                ref = link["inwardIssue"]
                linked.append(f"{ref.get('key', '?')} ({link_type})")
            if "outwardIssue" in link:
                ref = link["outwardIssue"]
                linked.append(f"{ref.get('key', '?')} ({link_type})")

        # Build the Jira browse URL from the server URL
        base_url = self.config.mcp_jira_server_url.replace("/mcp", "")
        ticket_url = f"{base_url}/browse/{ticket_id}"

        return JiraTicket(
            ticket_id=ticket_id,
            summary=fields.get("summary", ""),
            status=status_obj.get("name", ""),
            assignee=assignee_obj.get("displayName", "Unassigned"),
            reporter=reporter_obj.get("displayName", ""),
            priority=priority_obj.get("name", ""),
            issue_type=issuetype_obj.get("name", ""),
            created=_parse_jira_date(fields.get("created", "")),
            updated=_parse_jira_date(fields.get("updated", "")),
            description=_extract_text(fields.get("description", "")),
            linked_issues=linked,
            labels=fields.get("labels", []),
            components=[c.get("name", "") for c in fields.get("components", [])],
            url=ticket_url,
        )

    def _parse_jira_comments(self, data: dict) -> list[str]:
        """
        Parse Jira comments response into list of comment body strings.
        Returns most recent MAX_COMMENTS comments, newest first.
        """
        comments = data.get("comments", [])
        result: list[str] = []

        for comment in comments[: self.MAX_COMMENTS]:
            body = _extract_text(comment.get("body", ""))
            author = (comment.get("author") or {}).get("displayName", "Unknown")
            created = _parse_jira_date(comment.get("created", ""))
            if body:
                result.append(f"[{author}, {created}]: {body}")

        return result

    # ── Confluence Response Parsers ────────────────────────────────────────────

    def _parse_confluence_page(
        self,
        data: dict,
        original_ref: str,
    ) -> ConfluencePage:
        """
        Parse raw Confluence MCP response into a ConfluencePage.
        """
        space_obj = data.get("space") or {}
        version_obj = data.get("version") or {}
        author_obj = version_obj.get("by") or {}

        # Extract plain text content from body
        body = data.get("body", {})
        content = (
            body.get("plain", {}).get("value", "")
            or body.get("storage", {}).get("value", "")
            or body.get("view", {}).get("value", "")
            or ""
        )

        # Strip any residual HTML tags if storage format leaked through
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s{2,}", " ", content).strip()

        return ConfluencePage(
            page_id=str(data.get("id", "")),
            title=data.get("title", ""),
            space=space_obj.get("key", ""),
            url=data.get("_links", {}).get("webui", original_ref),
            content=content,
            last_modified=_parse_jira_date(version_obj.get("when", "")),
            author=author_obj.get("displayName", ""),
        )

    # ── URL / ID Helpers ───────────────────────────────────────────────────────

    def _extract_confluence_page_id(self, url_or_id: str) -> str | None:
        """
        Extract numeric page ID from a Confluence URL.

        Handles formats:
          https://org.atlassian.net/wiki/spaces/ENG/pages/12345678/Title
          https://org.atlassian.net/wiki/pages/viewpage.action?pageId=12345678
          12345678  (raw ID — returned as-is)

        Returns None if no page ID can be extracted.
        """
        # Raw numeric ID
        if url_or_id.isdigit():
            return url_or_id

        # /pages/{id}/ pattern
        match = re.search(r"/pages/(\d+)", url_or_id)
        if match:
            return match.group(1)

        # ?pageId= pattern
        match = re.search(r"[?&]pageId=(\d+)", url_or_id)
        if match:
            return match.group(1)

        return None


# ── Module-Level Helpers ───────────────────────────────────────────────────────


def _parse_jira_date(raw: str) -> str:
    """
    Parse Jira ISO timestamp into a readable date string.

    Jira returns: "2026-03-15T14:32:00.000+0000"
    We return:    "2026-03-15"

    Returns raw string unchanged if it doesn't match expected format.
    """
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def _extract_text(content: Any) -> str:
    """
    Extract plain text from Jira/Confluence content fields.

    Jira description and comment bodies can be:
      - A plain string (older API versions)
      - An Atlassian Document Format (ADF) dict (newer API versions)
      - None (unset field)

    This handles all three cases gracefully.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        # Atlassian Document Format — walk the content tree
        return _extract_adf_text(content).strip()

    return str(content)


def _extract_adf_text(node: dict, depth: int = 0) -> str:
    """
    Recursively extract plain text from an Atlassian Document Format (ADF) node.

    ADF is a JSON tree where text lives in leaf nodes of type "text".
    Block nodes like "paragraph", "heading", "listItem" contain child nodes.

    Max recursion depth of 20 — prevents pathological inputs from
    causing stack overflow on deeply nested documents.
    """
    if depth > 20:
        return ""

    node_type = node.get("type", "")
    result_parts: list[str] = []

    # Text leaf node — the actual content
    if node_type == "text":
        return node.get("text", "")

    # Block nodes with children
    children = node.get("content", [])
    for child in children:
        if isinstance(child, dict):
            text = _extract_adf_text(child, depth + 1)
            if text:
                result_parts.append(text)

    # Add newline after block-level elements for readability
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

    return separator.join(result_parts) + separator
