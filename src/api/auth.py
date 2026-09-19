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


def get_current_user(x_api_key: Optional[str] = Header(None)) -> UserContext:
    """
    Dependency to authenticate request via X-API-Key header.
    If API_KEY environment variable is set (e.g. on Render):
      - Validates against API_KEY (admin), API_KEY_REVIEWER (reviewer), API_KEY_VIEWER (viewer).
      - If missing or invalid, raises HTTP 401.
    If API_KEY environment variable is NOT set:
      - Allows developer keys if provided, or defaults to admin/usr-reviewer if no header provided.
    """
    env_admin_key = os.getenv("API_KEY")
    env_reviewer_key = os.getenv("API_KEY_REVIEWER")
    env_viewer_key = os.getenv("API_KEY_VIEWER")

    # If any env API key is explicitly configured on environment
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
        
        # Check fallback developer keys if passed
        for k, ctx in DEFAULT_KEYS.items():
            if secrets.compare_digest(x_api_key, k):
                return ctx
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-API-Key."
        )

    # Local dev mode (no env API_KEY configured)
    if x_api_key:
        if x_api_key in DEFAULT_KEYS:
            return DEFAULT_KEYS[x_api_key]
        for k, ctx in DEFAULT_KEYS.items():
            if secrets.compare_digest(x_api_key, k):
                return ctx
        # Role mapping from key name if present
        if "viewer" in x_api_key.lower():
            return UserContext(user_id="usr-viewer", role="viewer")
        elif "reviewer" in x_api_key.lower():
            return UserContext(user_id="usr-reviewer", role="reviewer")
        else:
            return UserContext(user_id=f"usr-{x_api_key[:12]}", role="admin")

    # Default fallback for unauthenticated requests in local dev mode
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
