"""Session state: resumption and forking (Domain 1.7).

A session is persisted as a `SessionManifest` -- a structured summary of the
work that completed (the normalized clauses, the liability slice, the verified
`Review`), NOT the raw message/tool-result transcript. Rebuilding a session from
structured state is more reliable than resuming with stale tool results, and it
is the same discipline as the rest of this example: carry real structured state,
not a replay of narration.

`SessionStore.resume` mirrors the SDK's `resume=<session_id>`; `fork` mirrors
`fork_session=True` (branch a new session id from a shared baseline). Resuming
against a changed document is refused, mirroring the need to inform a resumed
session that a previously analyzed file has changed.
"""

from pydantic import BaseModel, Field

from contract_review.schemas import Clause, Review
from contract_review.state import CoordinatorState


class StaleSessionError(RuntimeError):
    """Raised when resuming a session whose document no longer matches."""


class SessionManifest(BaseModel):
    """A structured summary of one session's completed work -- the case facts,
    not the conversation transcript."""

    session_id: str
    contract_id: str
    source_name: str
    doc_sha256: str
    normalized_clauses: list[Clause] = Field(default_factory=list)
    liability_clauses: list[Clause] = Field(default_factory=list)
    extractor_completed: bool = False
    reviews: dict[str, Review] = Field(default_factory=dict)


def _manifest_from_state(session_id: str, state: CoordinatorState) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        contract_id=state.contract_id,
        source_name=state.source_name,
        doc_sha256=state.doc_sha256,
        normalized_clauses=state.normalized_clauses,
        liability_clauses=state.liability_clauses,
        extractor_completed=state.extractor_completed,
        reviews=state.reviews,
    )


def _state_from_manifest(manifest: SessionManifest) -> CoordinatorState:
    state = CoordinatorState(
        contract_id=manifest.contract_id,
        source_name=manifest.source_name,
        doc_sha256=manifest.doc_sha256,
    )
    state.normalized_clauses = [c.model_copy(deep=True) for c in manifest.normalized_clauses]
    state.liability_clauses = [c.model_copy(deep=True) for c in manifest.liability_clauses]
    state.extractor_completed = manifest.extractor_completed
    state.reviews = {cid: r.model_copy(deep=True) for cid, r in manifest.reviews.items()}
    return state


class SessionStore:
    """Persists session manifests and rebuilds `CoordinatorState` from them."""

    def __init__(self) -> None:
        self._manifests: dict[str, SessionManifest] = {}

    def save(self, session_id: str, state: CoordinatorState) -> SessionManifest:
        # Store an independent snapshot so later mutation of `state` cannot bleed in.
        manifest = _manifest_from_state(session_id, state).model_copy(deep=True)
        self._manifests[session_id] = manifest
        return manifest

    def resume(self, session_id: str, *, doc_sha256: str) -> CoordinatorState:
        if session_id not in self._manifests:
            raise KeyError(f"unknown session: {session_id!r}")
        manifest = self._manifests[session_id]
        if manifest.doc_sha256 != doc_sha256:
            raise StaleSessionError(
                f"session {session_id!r} reviewed a different document "
                f"({manifest.doc_sha256!r}); the current document has changed."
            )
        return _state_from_manifest(manifest)

    def fork(self, session_id: str, new_session_id: str) -> SessionManifest:
        if session_id not in self._manifests:
            raise KeyError(f"unknown session: {session_id!r}")
        forked = self._manifests[session_id].model_copy(
            deep=True, update={"session_id": new_session_id}
        )
        self._manifests[new_session_id] = forked
        return forked

    def sessions(self) -> list[str]:
        return list(self._manifests)
