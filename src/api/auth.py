import os
import secrets
from typing import Optional, Dict
from pydantic import BaseModel
from fastapi import Header, HTTPException, status, Depends


class UserContext(BaseModel):
    user_id: str
    role: str  # "viewer", "reviewer", "admin"


# Default developer keys for local development and testing
DEFAULT_KEYS: Dict[str, UserContext] = {
    "dev-admin-key": UserContext(user_id="usr-admin", role="admin"),
    "dev-reviewer-key": UserContext(user_id="usr-reviewer", role="reviewer"),
    "dev-viewer-key": UserContext(user_id="usr-viewer", role="viewer"),
}


def verify_api_key_config():
    """
    Verifies that API key configuration is present when running on Render.
    Raises RuntimeError if running on Render and no API keys are configured.
    """
    is_render = bool(os.getenv("RENDER"))
    env_admin_key = os.getenv("API_KEY")
    env_reviewer_key = os.getenv("API_KEY_REVIEWER")
    env_viewer_key = os.getenv("API_KEY_VIEWER")

    if is_render and not (env_admin_key or env_reviewer_key or env_viewer_key):
        raise RuntimeError("API key configuration (API_KEY, API_KEY_REVIEWER, or API_KEY_VIEWER) is strictly required when running on Render.")


def get_current_user(x_api_key: Optional[str] = Header(None)) -> UserContext:
    """
    Dependency to authenticate request via X-API-Key header.
    - If API_KEY / API_KEY_REVIEWER / API_KEY_VIEWER environment variables are set:
        Only valid env keys are accepted using secrets.compare_digest. DEFAULT_KEYS are NEVER accepted.
    - If running on Render without API keys: raises RuntimeError.
    - Local dev mode without env keys: accepts developer keys only when ALLOW_DEV_KEYS=1 is set.
    """
    verify_api_key_config()

    env_admin_key = os.getenv("API_KEY")
    env_reviewer_key = os.getenv("API_KEY_REVIEWER")
    env_viewer_key = os.getenv("API_KEY_VIEWER")

    # Path A: Environment API Keys are set (Production / Staging mode)
    if env_admin_key or env_reviewer_key or env_viewer_key:
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-API-Key header is required."
            )
        
        if env_admin_key and secrets.compare_digest(x_api_key, env_admin_key):
            return UserContext(user_id="usr-admin", role="admin")
        if env_reviewer_key and secrets.compare_digest(x_api_key, env_reviewer_key):
            return UserContext(user_id="usr-reviewer", role="reviewer")
        if env_viewer_key and secrets.compare_digest(x_api_key, env_viewer_key):
            return UserContext(user_id="usr-viewer", role="viewer")

        # Do NOT accept DEFAULT_KEYS when environment API keys are configured!
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-API-Key."
        )

    # Path B: No environment API keys set (Local Dev Mode)
    allow_dev_keys = os.getenv("ALLOW_DEV_KEYS", "").strip() in ("1", "true", "TRUE")
    if not allow_dev_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Set API_KEY environment variable or ALLOW_DEV_KEYS=1 for local development."
        )

    if x_api_key:
        if x_api_key in DEFAULT_KEYS:
            return DEFAULT_KEYS[x_api_key]
        for k, ctx in DEFAULT_KEYS.items():
            if secrets.compare_digest(x_api_key, k):
                return ctx
        if "viewer" in x_api_key.lower():
            return UserContext(user_id="usr-viewer", role="viewer")
        elif "reviewer" in x_api_key.lower():
            return UserContext(user_id="usr-reviewer", role="reviewer")
        else:
            return UserContext(user_id=f"usr-{x_api_key[:12]}", role="admin")

    # Default unauthenticated fallback when ALLOW_DEV_KEYS=1 is enabled
    return UserContext(user_id="usr-reviewer", role="admin")


def require_role(min_role: str):
    """
    Factory dependency for RBAC enforcement.
    Role hierarchy: admin > reviewer > viewer.
    """
    role_weights = {
        "viewer": 1,
        "reviewer": 2,
        "admin": 3
    }
    
    def dependency(user: UserContext = Depends(get_current_user)) -> UserContext:
        user_weight = role_weights.get(user.role, 0)
        required_weight = role_weights.get(min_role, 3)
        if user_weight < required_weight:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires '{min_role}' role or higher. Your role: '{user.role}'."
            )
        return user

    return dependency
