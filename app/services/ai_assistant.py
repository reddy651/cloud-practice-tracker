import base64
import json
import os
import urllib.request
import urllib.error


def _gemini_request(contents, model=None):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": contents, "generationConfig": {"temperature": 0.2}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Gemini API error {exc.code}: {detail[:800]}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"], model
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Gemini returned an unexpected response: {data}") from exc


def suggest_step(image_bytes, content_type, context=""):
    encoded = base64.b64encode(image_bytes).decode("ascii")
    prompt = f"""You are helping a user document one IT practice screenshot. Suggest ONE short sentence describing what the user likely did in this screenshot. Do not invent actions. Prioritize visible selection, hover/highlight, typed values, buttons, navigation, and configuration. If the screenshot does not prove an action, say what is visibly shown rather than guessing. Keep it concise. This is only a suggestion that the user can edit. Practice context: {context or 'not provided'}."""
    contents = [{"role": "user", "parts": [{"text": prompt}, {"inline_data": {"mime_type": content_type, "data": encoded}}]}]
    return _gemini_request(contents)


def ask_ai(question, history=None):
    system = "You are a helpful public IT learning assistant. Answer questions about Azure, AWS, Linux, DevOps, networking, Docker, Kubernetes, Terraform, Ansible, VMware, databases and related infrastructure topics. Be accurate, practical and concise. If a question is ambiguous, state the assumption. Do not claim to have access to the user's systems."
    parts = [{"text": system}]
    if history:
        for item in history[-8:]:
            role = "user" if item.get("role") == "user" else "model"
            parts.append({"text": f"{role}: {item.get('text','')}"})
    parts.append({"text": f"user: {question}"})
    return _gemini_request([{"role": "user", "parts": parts}])
