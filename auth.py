"""
Google OAuth 2.0 authentication for the Manager Tool Streamlit app.

Setup:
    1. Go to https://console.cloud.google.com/apis/credentials
    2. Create an OAuth 2.0 Client ID (Web application)
    3. Add authorized redirect URI: http://localhost:8501/
       (or your deployed URL)
    4. Set environment variables or use the Configuration page:
        GOOGLE_CLIENT_ID=<your-client-id>
        GOOGLE_CLIENT_SECRET=<your-client-secret>

    Optionally restrict access to specific email domains or addresses
    via the ALLOWED_EMAILS or ALLOWED_DOMAIN environment variables.
"""

import os
import json
import hashlib
import secrets
from urllib.parse import urlencode

import streamlit as st
import requests

import database as db

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_oauth_config():
    """Return (client_id, client_secret) from env vars or database config."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID") or db.get_config("google_client_id")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or db.get_config("google_client_secret")
    return client_id, client_secret


def _get_redirect_uri():
    """Build the OAuth redirect URI from the current Streamlit URL."""
    return os.environ.get(
        "OAUTH_REDIRECT_URI",
        db.get_config("oauth_redirect_uri", "http://localhost:8501/"),
    )


def _is_email_allowed(email):
    """Check if the email is allowed to access the app."""
    # Check allowed emails list (comma-separated)
    allowed_emails = os.environ.get("ALLOWED_EMAILS") or db.get_config("allowed_emails")
    if allowed_emails:
        emails = [e.strip().lower() for e in allowed_emails.split(",")]
        if email.lower() in emails:
            return True

    # Check allowed domain
    allowed_domain = os.environ.get("ALLOWED_DOMAIN") or db.get_config("allowed_domain")
    if allowed_domain:
        domain = email.lower().split("@")[-1]
        if domain == allowed_domain.lower().strip():
            return True

    # If neither is configured, allow all authenticated Google users
    if not allowed_emails and not allowed_domain:
        return True

    return False


# ---------------------------------------------------------------------------
# OAuth 2.0 flow
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = "openid email profile"


def _build_auth_url(client_id, redirect_uri):
    """Generate the Google OAuth authorization URL with PKCE / state."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)
    st.session_state["oauth_state"] = state
    st.session_state["oauth_nonce"] = nonce

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _exchange_code(code, client_id, client_secret, redirect_uri):
    """Exchange the authorization code for tokens and user info."""
    token_resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    token_resp.raise_for_status()
    tokens = token_resp.json()

    # Fetch user profile
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_resp = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=10)
    user_resp.raise_for_status()
    return user_resp.json()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def is_authenticated():
    """Return True if the current session has a logged-in user."""
    return st.session_state.get("authenticated", False)


def get_current_user():
    """Return the current user dict or None."""
    return st.session_state.get("user")


def logout():
    """Clear the authentication session state."""
    for key in ["authenticated", "user", "oauth_state", "oauth_nonce"]:
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Login page rendering
# ---------------------------------------------------------------------------

def _render_login_page():
    """Render a clean, user-friendly login page."""
    # Center the login card
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown(
            """
            <div style="text-align: center; padding: 2rem 0 1rem 0;">
                <h1 style="margin-bottom: 0.2rem;">Manager Tool</h1>
                <p style="color: gray; font-size: 1.1rem;">
                    Sign in to manage your team
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        client_id, client_secret = _get_oauth_config()

        if not client_id or not client_secret:
            st.warning(
                "Google OAuth is not configured yet.  \n"
                "Set **GOOGLE_CLIENT_ID** and **GOOGLE_CLIENT_SECRET** "
                "as environment variables, or expand the section below "
                "to configure them now."
            )
            with st.expander("Configure Google OAuth Credentials"):
                st.markdown(
                    "1. Go to [Google Cloud Console]"
                    "(https://console.cloud.google.com/apis/credentials)  \n"
                    "2. Create an **OAuth 2.0 Client ID** "
                    "(Application type: *Web application*)  \n"
                    "3. Add **Authorized redirect URI**: "
                    "`http://localhost:8501/`  \n"
                    "4. Copy the Client ID and Client Secret below."
                )
                with st.form("oauth_config_form"):
                    cid = st.text_input("Google Client ID")
                    csec = st.text_input("Google Client Secret", type="password")
                    redirect = st.text_input(
                        "Redirect URI", value="http://localhost:8501/"
                    )
                    allowed = st.text_input(
                        "Allowed emails (comma-separated, blank = all)",
                        placeholder="alice@company.com, bob@company.com",
                    )
                    domain = st.text_input(
                        "Allowed domain (blank = any)",
                        placeholder="company.com",
                    )
                    if st.form_submit_button("Save"):
                        if cid and csec:
                            db.set_config("google_client_id", cid)
                            db.set_config("google_client_secret", csec)
                            db.set_config("oauth_redirect_uri", redirect)
                            if allowed:
                                db.set_config("allowed_emails", allowed)
                            if domain:
                                db.set_config("allowed_domain", domain)
                            st.success("Saved! Refresh the page to sign in.")
                            st.rerun()
                        else:
                            st.error("Client ID and Secret are required.")
            return

        redirect_uri = _get_redirect_uri()
        auth_url = _build_auth_url(client_id, redirect_uri)

        st.markdown("---")

        st.link_button(
            "\U0001F510  Sign in with Google",
            auth_url,
            use_container_width=True,
        )

        st.markdown(
            """
            <p style="text-align: center; color: gray; font-size: 0.85rem;
                       margin-top: 1.5rem;">
                You will be redirected to Google to authenticate.<br>
                Only authorized accounts can access this app.
            </p>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main auth gate — call this at the top of your app
# ---------------------------------------------------------------------------

def require_auth():
    """Gate the app behind Google OAuth.

    Returns True if the user is authenticated and should see the app.
    Returns False if the login page is being shown instead.
    """
    # Already logged in
    if is_authenticated():
        return True

    # Check for OAuth callback (code in query params)
    params = st.query_params
    code = params.get("code")
    state = params.get("state")

    if code and state:
        expected_state = st.session_state.get("oauth_state")
        if state != expected_state:
            st.error("Invalid OAuth state. Please try signing in again.")
            st.query_params.clear()
            _render_login_page()
            return False

        client_id, client_secret = _get_oauth_config()
        redirect_uri = _get_redirect_uri()

        try:
            user_info = _exchange_code(code, client_id, client_secret, redirect_uri)
        except Exception as exc:
            st.error(f"Authentication failed: {exc}")
            st.query_params.clear()
            _render_login_page()
            return False

        email = user_info.get("email", "")
        if not _is_email_allowed(email):
            st.error(
                f"Access denied for **{email}**.  \n"
                "Contact your administrator to get access."
            )
            st.query_params.clear()
            _render_login_page()
            return False

        # Store user in session and DB
        st.session_state["authenticated"] = True
        st.session_state["user"] = {
            "email": email,
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
            "sub": user_info.get("sub", ""),
        }
        db.upsert_user(
            google_id=user_info.get("sub", ""),
            email=email,
            name=user_info.get("name", ""),
            picture=user_info.get("picture", ""),
        )

        # Clear query params and reload
        st.query_params.clear()
        st.rerun()

    # No code — show login page
    _render_login_page()
    return False
