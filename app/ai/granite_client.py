"""
granite_client.py
-----------------
IBM watsonx.ai Granite API client.

Responsibilities
----------------
- Load credentials from environment variables.
- Authenticate through IBM Cloud IAM.
- Send prompts to IBM Granite inference endpoint.
- Retry on temporary failures.
- Return typed GraniteResponse.
- Fail gracefully when Granite is unavailable.

This module only handles AI transport.
DSP and engineering logic are handled elsewhere.
"""

import logging
import os
import time
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

_DEFAULT_MODEL_ID = "ibm/granite-4-h-small"

_IAM_TOKEN_URL = (
    "https://iam.cloud.ibm.com/identity/token"
)

_GENERATE_PATH = (
    "/ml/v1/text/generation?version=2023-05-29"
)


_REQUEST_TIMEOUT_SEC = 30
_MAX_RETRIES = 3
_RETRY_BACKOFF_SEC = 2


_MAX_NEW_TOKENS = 800
_MIN_NEW_TOKENS = 20
_TEMPERATURE = 0.2
_TOP_P = 0.85
_REPETITION_PENALTY = 1.1


# ============================================================
# Response Object
# ============================================================

@dataclass
class GraniteResponse:

    available: bool
    text: str

    model_id: str = _DEFAULT_MODEL_ID

    input_tokens: int = 0
    output_tokens: int = 0

    stop_reason: str = "unavailable"

    error_message: str = ""

    latency_ms: int = 0



# ============================================================
# IAM Token Cache
# ============================================================

_token_cache = {
    "token": None,
    "expires_at": 0
}



def _get_iam_token(api_key: str) -> str:

    now = time.time()

    if (
        _token_cache["token"]
        and now < _token_cache["expires_at"] - 60
    ):
        return _token_cache["token"]


    try:

        response = requests.post(

            _IAM_TOKEN_URL,

            data={
                "grant_type":
                "urn:ibm:params:oauth:grant-type:apikey",

                "apikey": api_key
            },

            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },

            timeout=15
        )


        response.raise_for_status()

        data = response.json()


        _token_cache["token"] = data["access_token"]

        _token_cache["expires_at"] = (
            now + int(data.get("expires_in", 3600))
        )


        return _token_cache["token"]


    except Exception as exc:

        raise RuntimeError(
            f"IAM token exchange failed: {exc}"
        )
        # ============================================================
# Granite Generate Function
# ============================================================

def generate(
    prompt: str,
    max_new_tokens: int = _MAX_NEW_TOKENS,
    temperature: float = _TEMPERATURE,
) -> GraniteResponse:


    api_key = os.environ.get(
        "IBM_WATSONX_API_KEY",
        ""
    ).strip()


    project_id = os.environ.get(
        "IBM_WATSONX_PROJECT_ID",
        ""
    ).strip()


    base_url = os.environ.get(
        "IBM_WATSONX_URL",
        ""
    ).strip().rstrip("/")


    model_id = os.environ.get(
        "IBM_GRANITE_MODEL_ID",
        _DEFAULT_MODEL_ID
    ).strip()



    # -------------------------------
    # Validate Environment
    # -------------------------------

    if not all(
        [
            api_key,
            project_id,
            base_url
        ]
    ):

        missing = []

        if not api_key:
            missing.append(
                "IBM_WATSONX_API_KEY"
            )

        if not project_id:
            missing.append(
                "IBM_WATSONX_PROJECT_ID"
            )

        if not base_url:
            missing.append(
                "IBM_WATSONX_URL"
            )


        return GraniteResponse(

            available=False,

            text="",

            model_id=model_id,

            error_message=(
                f"Missing environment variables: {missing}"
            ),

            stop_reason="unavailable"

        )



    # -------------------------------
    # IAM Authentication
    # -------------------------------

    try:

        token = _get_iam_token(api_key)


    except Exception as exc:

        return GraniteResponse(

            available=False,

            text="",

            model_id=model_id,

            error_message=str(exc),

            stop_reason="error"

        )



    endpoint = (
        base_url +
        _GENERATE_PATH
    )


    headers = {

        "Authorization":
            f"Bearer {token}",

        "Content-Type":
            "application/json",

        "Accept":
            "application/json"

    }



    payload = {

        "model_id":
            model_id,


        "input":
            prompt,


        "project_id":
            project_id,


        "parameters":

        {

            "decoding_method":
                "sample",


            "max_new_tokens":
                max_new_tokens,


            "min_new_tokens":
                _MIN_NEW_TOKENS,


            "temperature":
                temperature,


            "top_p":
                _TOP_P,


            "repetition_penalty":
                _REPETITION_PENALTY

        }

    }



    last_error = ""



    # -------------------------------
    # Retry Logic
    # -------------------------------

    for attempt in range(1, _MAX_RETRIES + 1):

        start = time.time()


        try:

            response = requests.post(

                endpoint,

                json=payload,

                headers=headers,

                timeout=_REQUEST_TIMEOUT_SEC

            )


            latency = int(
                (time.time() - start) * 1000
            )



            if response.status_code == 429:

                wait = (
                    _RETRY_BACKOFF_SEC *
                    (2 ** (attempt - 1))
                )

                time.sleep(wait)

                last_error = (
                    "Rate limited by IBM watsonx"
                )

                continue



            response.raise_for_status()



            data = response.json()



            results = data.get(
                "results",
                []
            )



            if not results:

                return GraniteResponse(

                    available=False,

                    text="",

                    model_id=model_id,

                    error_message=
                    "Empty Granite response",

                    stop_reason="error",

                    latency_ms=latency

                )



            result = results[0]



            generated = result.get(
                "generated_text",
                ""
            ).strip()



            # Remove Granite formatting artifacts

            generated = generated.replace(
                "<extra_id_1>",
                ""
            )


            generated = generated.replace(
                "Assistant",
                ""
            )


            generated = generated.strip()



            return GraniteResponse(

                available=True,

                text=generated,

                model_id=model_id,

                input_tokens=result.get(
                    "input_token_count",
                    0
                ),

                output_tokens=result.get(
                    "generated_token_count",
                    0
                ),

                stop_reason=result.get(
                    "stop_reason",
                    "eos_token"
                ),

                latency_ms=latency

            )



        except requests.exceptions.Timeout:

            last_error = (
                "Granite request timeout"
            )


        except requests.exceptions.ConnectionError as exc:

            last_error = (
                f"Connection error: {exc}"
            )


        except Exception as exc:

            last_error = (
                f"Unexpected error: {exc}"
            )

            break



        if attempt < _MAX_RETRIES:

            time.sleep(
                _RETRY_BACKOFF_SEC * attempt
            )



    return GraniteResponse(

        available=False,

        text="",

        model_id=model_id,

        error_message=last_error,

        stop_reason="error"

    )



# ============================================================
# Credential Health Check
# ============================================================

def check_credentials() -> dict:


    api_key = os.environ.get(
        "IBM_WATSONX_API_KEY",
        ""
    )


    project_id = os.environ.get(
        "IBM_WATSONX_PROJECT_ID",
        ""
    )


    base_url = os.environ.get(
        "IBM_WATSONX_URL",
        ""
    )


    model_id = os.environ.get(
        "IBM_GRANITE_MODEL_ID",
        _DEFAULT_MODEL_ID
    )


    return {

        "api_key_set":
            bool(api_key),


        "project_id_set":
            bool(project_id),


        "base_url_set":
            bool(base_url),


        "model_id":
            model_id,


        "all_configured":
            bool(
                api_key
                and project_id
                and base_url
            )

    }
def check_granite_available() -> bool:
    """
    Returns True only when IBM Granite credentials are configured.
    Used by integration tests and health checks.
    """
    status = check_credentials()
    return status.get("all_configured", False)