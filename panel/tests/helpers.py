"""Shared helpers for panel view tests."""


def page_without_csrf(response) -> str:
    """Decoded body with the CSRF token cut out.

    The token is 64 random letters and digits, so a short code such as ``B1`` turns up
    in it about once in 60 renders. Check ``"B1" not in page`` on this text, not on the
    raw body, or the test fails at random.
    """
    token = str(response.context["csrf_token"])
    return response.content.decode().replace(token, "")
