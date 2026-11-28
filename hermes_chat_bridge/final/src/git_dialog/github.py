"""Opt-in GitHub REST adapter. No credential discovery, retries or execution.

Append only PATCHes an existing ref, force=False. Explicit new-dialog creation
uses a separate, durably granted POST of a new ref; never append-as-create.
Offline contract tests are NOT a live GitHub conformance test.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import (DialogError, Message, PROTOCOL, VERSION, TaskState, branch_ref, message_file_path,
               parse_message, parse_snapshot, prepare_append, validate_rel_path)

LIMIT = 2 * 1024 * 1024
HISTORY_LIMIT = 8 * 1024 * 1024
# Process-only cache: no disk, always re-check live private repo/ref before reuse.
SNAPSHOT_CACHE_ENTRIES = 8
MAX_PARALLEL_BLOBS = 4
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")


def sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise DialogError("invalid_sha")
    return value


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)


def blob_sha(content):
    raw = content.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def decode_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        if len(raw) > LIMIT:
            raise ValueError("size")
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError):
        raise DialogError("invalid_json") from None


class ApiError(Exception):
    """Only a sanitized status; never response bodies, URLs or credentials."""
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"github_http_{status}" if status else "github_transport_error")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonHttp:
    """Explicitly supplied authorization only; never reads env, git or files.

    Construction does not make a request. Redirects and automatic retries are
    forbidden. The caller must arrange approved, repository-scoped credentials.
    """
    def __init__(self, token, *, timeout=15, budget_path=None):
        if (not isinstance(token, str) or not token or
                any(not 33 <= ord(c) <= 126 for c in token)):
            raise DialogError("authorization_required")
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise DialogError("invalid_timeout")
        self._token = token
        self._timeout = timeout
        # Separate urllib opener per worker; never share mutable handler state.
        self._thread = threading.local()
        self._budget = None
        if budget_path is not None:
            from .api_budget import SharedApiBudget
            self._budget = SharedApiBudget(budget_path, token)

    def request(self, method, path, payload=None):
        # Paths are supplied only by the repository-bound adapter. Never
        # accept URL injection or forward Authorization across redirects.
        if (method not in ("GET", "POST", "PATCH", "DELETE") or
                not isinstance(path, str) or not re.fullmatch(r"/repos/[A-Za-z0-9_./?=-]+", path) or
                any(p in (".", "..") for p in path.split("/"))):
            raise DialogError("invalid_api_request")
        from .api_budget import _immutable, _mutable
        budget = self._budget
        if budget is not None:
            budget.gate()  # fail fast; never spin on a depleted quota
        saved = budget.cached(path) if budget is not None and method == "GET" else None
        if method == "GET" and saved is not None and _immutable(path):
            # Immutable Git SHA object. GitHubDialog still authenticates the
            # repo/ref on each load; it also verifies content/hash linkage.
            return decode_json(saved[1])
        headers = {"Authorization": "Bearer " + self._token,
                   "Accept": "application/vnd.github+json",
                   "Content-Type": "application/json",
                   "X-GitHub-Api-Version": "2026-03-10",
                   "User-Agent": "git-dialog-v1"}
        if (method == "GET" and saved is not None and _mutable(path) and saved[0]):
            headers["If-None-Match"] = saved[0]
        data = None if payload is None else canonical(payload).encode("utf-8")
        req = Request("https://api.github.com" + path, data=data, method=method,
                      headers=headers)
        try:
            opener = getattr(self._thread, "opener", None)
            if opener is None:
                opener = build_opener(ProxyHandler({}), NoRedirect())
                self._thread.opener = opener
            with opener.open(req, timeout=self._timeout) as response:
                raw = response.read(LIMIT + 1)
                status = response.status
                if budget is not None:
                    # Never cache an oversized body, redirect or failed reply.
                    budget.observe(response.headers, status=status,
                                   path=path if method == "GET" and status == 200 else None,
                                   data=raw.decode("utf-8") if method == "GET" and
                                        status == 200 and len(raw) <= LIMIT else None)
                if status not in (200, 201, 204):
                    raise ApiError(status)
        except HTTPError as exc:
            status = exc.code
            response_headers = exc.headers
            if budget is not None:
                budget.observe(response_headers, status=status)
            exc.close()
            if status == 304 and method == "GET" and saved is not None and _mutable(path):
                # Authenticated origin validated the ETag. GitHub 304 does not
                # consume the primary core quota; never return stale cache
                # on a 403/404/429 or when there was no matching 304.
                return decode_json(saved[1])
            if status in (403, 429) and budget is not None:
                try:
                    budget.gate()
                except DialogError as err:
                    if err.code == "github_rate_limited":
                        raise err from None
            raise ApiError(status) from None
        except (URLError, OSError, TimeoutError):
            raise ApiError() from None
        return None if method == "DELETE" and not raw else decode_json(raw)


@dataclass(frozen=True)
class Receipt:
    commit_sha: str  # verified reachable snapshot containing this exact message
    message: Message
    created: bool


class GitHubDialog:
    """Bound private repository; separate explicit creation, update-only append.

    Fast-forward sibling exclusion relies on commits having ONE expected parent,
    all writers using this contract, no external ref rewind/recreation (ABA).
    A future lifecycle registry must prevent reuse of deleted task IDs.
    """
    def __init__(self, transport, repository="S960b/agent-dialogs"):
        if (not isinstance(repository, str) or not REPO.fullmatch(repository) or
                any(p in (".", "..") for p in repository.split("/"))):
            raise DialogError("invalid_repository")
        self.transport = transport
        self.repository = repository
        self.base = "/repos/" + repository
        self._snapshots = OrderedDict()

    def _request(self, method, suffix="", payload=None):
        return self.transport.request(method, self.base + suffix, payload)

    def _private(self):
        repo = self._request("GET")
        if (not isinstance(repo, dict) or repo.get("private") is not True or
                not isinstance(repo.get("full_name"), str) or
                repo["full_name"].lower() != self.repository.lower()):
            raise DialogError("private_repository_required")

    def _head(self, task_id):
        ref = branch_ref(task_id)
        try:
            result = self._request("GET", "/git/ref/heads/" + ref)
        except ApiError as exc:
            if exc.status == 404:
                # Could also mean access was revoked. NEVER attempt create.
                raise DialogError("branch_missing_or_inaccessible") from None
            raise
        if (not isinstance(result, dict) or result.get("ref") != "refs/heads/" + ref or
                result.get("object", {}).get("type") != "commit"):
            raise DialogError("invalid_ref_response")
        return sha(result["object"].get("sha"))

    def delete_branch(self, task_id, expected_head):
        """Delete only the exact registered dialogue ref (not a conditional Git API).

        Caller MUST first persist a tombstone in the lifecycle registry.
        No automatic retry if DELETE result or follow-up observation is uncertain.
        """
        sha(expected_head)
        self._private()
        if self._head(task_id) != expected_head:
            raise DialogError('delete_head_changed')
        try:
            response = self._request("DELETE", "/git/refs/heads/" + branch_ref(task_id))
            if response is not None:
                raise DialogError('delete_unconfirmed')
        except ApiError:
            raise DialogError('delete_unconfirmed') from None
        try:
            self._private()
            self._head(task_id)
        except DialogError as exc:
            if exc.code == 'branch_missing_or_inaccessible':
                return True
            raise DialogError('delete_unconfirmed') from None
        except ApiError:
            raise DialogError('delete_unconfirmed') from None
        raise DialogError('delete_unconfirmed')
    def _commit(self, head):
        obj = self._request("GET", "/git/commits/" + sha(head))
        if not isinstance(obj, dict) or obj.get("sha") != head:
            raise DialogError("invalid_commit_response")
        sha(obj.get("tree", {}).get("sha"))
        return obj

    def _tree(self, tree_sha):
        obj = self._request("GET", "/git/trees/" + sha(tree_sha) + "?recursive=1")
        if (not isinstance(obj, dict) or obj.get("sha") != tree_sha or
                obj.get("truncated") is not False or not isinstance(obj.get("tree"), list) or
                len(obj["tree"]) > 10000):
            raise DialogError("invalid_or_truncated_tree")
        entries = {}
        for entry in obj["tree"]:
            if not isinstance(entry, dict):
                raise DialogError("invalid_tree_entry")
            path = validate_rel_path(entry.get("path"))
            if path in entries:
                raise DialogError("duplicate_tree_path")
            entries[path] = {k: entry.get(k) for k in ("mode", "type", "sha")}
            sha(entry.get("sha"))
        return entries

    def _blob(self, entry):
        if entry.get("type") != "blob" or entry.get("mode") != "100644":
            raise DialogError("not_regular_file")
        blob_sha = sha(entry.get("sha"))
        obj = self._request("GET", "/git/blobs/" + blob_sha)
        try:
            if obj.get("sha") != blob_sha or obj.get("encoding") != "base64":
                raise ValueError()
            encoded = obj["content"].replace("\n", "")
            if len(encoded) > LIMIT * 2:
                raise ValueError()
            raw = base64.b64decode(encoded, validate=True)
            digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if len(raw) > LIMIT or obj.get("size") != len(raw) or digest != blob_sha:
                raise ValueError()
            return decode_json(raw.decode("utf-8"))
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError):
            raise DialogError("invalid_blob") from None

    def _snapshot(self, task_id, head):
        commit = self._commit(head)
        tree = self._tree(commit["tree"]["sha"])
        if "task.json" not in tree or "state.json" not in tree:
            raise DialogError("dialog_files_missing")
        paths = [p for p, e in tree.items() if p.startswith("messages/") and e["type"] != "tree"]
        if len(paths) > 1000:
            raise DialogError("history_limit")
        entries = [tree["task.json"], tree["state.json"]] + [tree[p] for p in paths]
        # A strict four-request ceiling; only the real HTTP transport uses
        # parallel immutable blob downloads. Offline contract doubles keep
        # deterministic serial order. No retries, redirects, or bypassed hashes.
        if isinstance(self.transport, JsonHttp) and len(entries) > 3:
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_BLOBS,
                                    thread_name_prefix="git-dialog-read") as pool:
                blobs = list(pool.map(self._blob, entries))
        else:
            blobs = [self._blob(entry) for entry in entries]
        task, state = blobs[:2]
        messages, size = [], 0
        for path, obj in zip(paths, blobs[2:]):
            message = parse_message(obj)
            if path != message_file_path(message.seq, message.sender):
                raise DialogError("invalid_message_paths")
            size += len(canonical(obj).encode("utf-8"))
            if size > HISTORY_LIMIT:
                raise DialogError("history_limit")
            messages.append(obj)
        try:
            messages.sort(key=lambda m: m["seq"])
        except (KeyError, TypeError):
            raise DialogError("invalid_history") from None
        snap = parse_snapshot(task, state, messages, head)
        if snap.state.task_id != task_id:
            raise DialogError("foreign_task")
        if set(paths) != {message_file_path(m.seq, m.sender) for m in snap.messages}:
            raise DialogError("invalid_message_paths")
        return snap, commit, tree

    def load(self, task_id):
        branch_ref(task_id)
        # Trust neither cached repo permissions nor a cached branch pointer.
        # Two fresh remote checks bound each UI read to an existing private
        # repository and the latest verified immutable commit.
        self._private()
        head = self._head(task_id)
        key = (task_id, head)
        cached = self._snapshots.get(key)
        if cached is not None:
            self._snapshots.move_to_end(key)
            return cached
        snapshot = self._snapshot(task_id, head)[0]
        # Cache only AFTER all Git object identities, contents and history
        # invariants pass. On a newer HEAD evict the old task snapshot.
        for old_key in tuple(self._snapshots):
            if old_key[0] == task_id:
                del self._snapshots[old_key]
        self._snapshots[key] = snapshot
        if len(self._snapshots) > SNAPSHOT_CACHE_ENTRIES:
            self._snapshots.popitem(last=False)
        return snapshot

    @staticmethod
    def creation_files(task_id, title, initiator, request_id):
        # Validation is shared with the durable creation intent, before I/O.
        from .dialog_catalog import creation_identity
        if creation_identity(initiator, request_id, title) != task_id:
            raise DialogError('creation_identity_mismatch')
        return {
            'task.json': {'protocol': PROTOCOL, 'version': VERSION, 'task_id': task_id,
                'title': title, 'initiator': initiator, 'creation_request_id': request_id},
            'state.json': TaskState(task_id, 0, 0, None, initiator, 'waiting').to_dict(),
        }

    def prepare_create(self, task_id, title, initiator, request_id):
        """Create and verify isolated objects, NOT a branch. No inherited history.

        Called only after a durable creation attempt is recorded. A 404 in
        append/load NEVER reaches this method or gives permission to recreate.
        """
        files = self.creation_files(task_id, title, initiator, request_id)
        self._private()
        try:
            self._head(task_id)
        except DialogError as exc:
            if exc.code != 'branch_missing_or_inaccessible':
                raise
        else:
            raise DialogError('dialog_already_exists')
        tree = self._request('POST', '/git/trees', {'tree': [
            {'path': path, 'mode': '100644', 'type': 'blob', 'content': canonical(value)}
            for path, value in files.items()]})
        tree_sha = sha(tree.get('sha'))
        commit = self._request('POST', '/git/commits', {
            'tree': tree_sha, 'parents': [], 'message': 'git-dialog create ' + task_id})
        candidate = sha(commit.get('sha'))
        self._verify_creation_objects(task_id, candidate, files)
        return candidate

    def _verify_creation_objects(self, task_id, candidate, files):
        initial, commit, tree = self._snapshot(task_id, sha(candidate))
        expected = {path: {'mode': '100644', 'type': 'blob', 'sha': blob_sha(canonical(obj))}
                    for path, obj in files.items()}
        if (commit.get('parents') != [] or tree != expected or initial.messages or
                initial.task != files['task.json'] or initial.state.to_dict() != files['state.json']):
            raise DialogError('creation_content_mismatch')

    def publish_create(self, task_id, candidate, title, initiator, request_id):
        """One new-ref POST. Never force/update an existing ref or retry."""
        files = self.creation_files(task_id, title, initiator, request_id)
        self._private()
        self._verify_creation_objects(task_id, candidate, files)
        try:
            result = self._request('POST', '/git/refs', {
                'ref': 'refs/heads/' + branch_ref(task_id), 'sha': sha(candidate)})
            if (not isinstance(result, dict) or result.get('ref') != 'refs/heads/' + branch_ref(task_id)
                    or result.get('object', {}).get('type') != 'commit'
                    or result['object'].get('sha') != candidate):
                raise DialogError('creation_unconfirmed')
        except (ApiError, DialogError, TypeError, AttributeError):
            raise DialogError('creation_unconfirmed') from None
        return self.observe_creation(task_id, candidate, title, initiator, request_id)

    def observe_creation(self, task_id, candidate, title, initiator, request_id):
        """GET-only exact creation reconciliation; absence never grants resend."""
        files = self.creation_files(task_id, title, initiator, request_id)
        self._private()
        self._verify_creation_objects(task_id, candidate, files)
        current = self._snapshot(task_id, self._head(task_id))[0]
        if current.task != files['task.json']:
            raise DialogError('creation_content_mismatch')
        return current

    def reconcile(self, task_id, message):
        """Read only. Not observed is NOT permission to execute the task again."""
        snap = self.load(task_id)
        for current in snap.messages:
            if current.message_id == message.message_id:
                if current != message:
                    raise DialogError("message_id_conflict")
                return Receipt(snap.head, current, False)
        raise DialogError("publication_not_observed")

    def append(self, task_id, expected_head, *, message_id, actor, kind, body,
               created_at_utc=None):
        branch_ref(task_id)
        sha(expected_head)
        self._private()
        head = self._head(task_id)
        if head != expected_head:
            raise DialogError("stale_head")
        snap, commit, old_tree = self._snapshot(task_id, head)
        prepared = prepare_append(snap, head, task_id=task_id, message_id=message_id,
                                  actor=actor, kind=kind, body=body,
                                  created_at_utc=created_at_utc)
        if not prepared.created:
            return Receipt(head, prepared.message, False)
        path = message_file_path(prepared.message.seq, prepared.message.sender)
        if path in old_tree:
            raise DialogError("immutable_message_exists")
        changes = {path: prepared.message.to_json(), "state.json": canonical(prepared.state.to_dict())}
        new_tree = self._request("POST", "/git/trees", {
            "base_tree": commit["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c}
                     for p, c in changes.items()]})
        tree_sha = sha(new_tree.get("sha"))
        new_commit = self._request("POST", "/git/commits", {
            "tree": tree_sha, "parents": [head],
            "message": f"git-dialog {task_id} {message_id}"})
        candidate = sha(new_commit.get("sha"))
        # Verify immutable history, tree, sole parent and both atomic files at
        # the candidate BEFORE exposing it through a branch ref.
        after, actual_commit, actual_tree = self._snapshot(task_id, candidate)
        parents = actual_commit.get("parents")
        if (not isinstance(parents, list) or len(parents) != 1 or
                not isinstance(parents[0], dict) or parents[0].get("sha") != head):
            raise DialogError("invalid_commit_parent")
        expected_unchanged = {p: e for p, e in old_tree.items() if p != "state.json" and e["type"] != "tree"}
        actual_unchanged = {p: e for p, e in actual_tree.items()
                            if p not in changes and e["type"] != "tree"}
        if (actual_commit["tree"]["sha"] != tree_sha or
                actual_unchanged != expected_unchanged or
                any(actual_tree.get(p) != {"mode": "100644", "type": "blob", "sha": blob_sha(c)}
                    for p, c in changes.items()) or
                after.task != snap.task or after.messages != snap.messages + [prepared.message] or
                after.state != prepared.state):
            raise DialogError("candidate_content_mismatch")
        if self._head(task_id) != head:
            raise DialogError("stale_head")
        try:
            result = self._request("PATCH", "/git/refs/heads/" + branch_ref(task_id),
                                   {"sha": candidate, "force": False})
            if (not isinstance(result, dict) or
                    result.get("ref") != "refs/heads/" + branch_ref(task_id) or
                    result.get("object", {}).get("sha") != candidate):
                raise DialogError("publication_unknown")
        except ApiError as exc:
            if exc.status in (401, 403, 404, 409, 422, 429):
                raise DialogError("ref_update_rejected", str(exc.status)) from None
            raise DialogError("publication_unknown") from None
        except (DialogError, AttributeError, TypeError):
            # Invalid/missing ACK may conceal an accepted write, too.
            raise DialogError("publication_unknown") from None
        # ACK alone is not confirmation; observe exact immutable message via
        # the current ref, allowing a legitimate later reply to have arrived.
        observed = self.reconcile(task_id, prepared.message)
        return Receipt(observed.commit_sha, prepared.message, True)

