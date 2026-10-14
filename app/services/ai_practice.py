import base64
import json
import os
from openai import OpenAI

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "provider": {"type": "string"},
        "service": {"type": "string"},
        "topic": {"type": "string"},
        "description": {"type": "string"},
        "notes": {"type": "string"},
        "problems": {"type": "string"},
        "solutions": {"type": "string"},
        "commands": {"type": "string"},
        "interview_questions": {"type": "string"}
    },
    "required": [
        "title", "provider", "service", "topic", "description", "notes",
        "problems", "solutions", "commands", "interview_questions"
    ]
}

INSTRUCTIONS = """
You are the documentation assistant for a Cloud Practice Tracker.
Analyze the supplied screenshots in their exact order. They are screenshots from one
technical practice session (Azure, AWS, Linux, Azure DevOps, DevOps, VMware, networking,
or another infrastructure topic).

Create accurate documentation based primarily on what is visible. Do not invent actions,
commands, resource names, values, errors, or steps that are not supported by the screenshots.
When something is not visible, say "Not visible in the screenshots" rather than guessing.
You may explain the technical meaning of a visible configuration using your general knowledge,
but clearly avoid claiming the user performed an action that is not evidenced.

Write concise but useful documentation suitable for the user's personal learning portfolio and
future interview preparation. Preserve exact commands, resource names, IPs, ports, rule
priorities, and configuration values when visible. If a screenshot contains secrets, passwords,
access tokens, private keys, or connection strings, redact them as [REDACTED] in the generated text.

Return all fields as plain text. Use numbered steps and bullets inside the relevant fields where useful.
"""


def _data_url(data, content_type):
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def analyze_screenshots(images):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to your .env file.")

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    client = OpenAI(api_key=api_key)

    content = [{"type": "input_text", "text": INSTRUCTIONS + "\nScreenshot order is significant."}]
    for index, item in enumerate(images, start=1):
        content.append({
            "type": "input_text",
            "text": f"Screenshot {index} of {len(images)} follows. Analyze it in sequence."
        })
        content.append({
            "type": "input_image",
            "image_url": _data_url(item["data"], item["content_type"]),
            "detail": "auto",
        })

    response = client.responses.create(
        model=model,
        store=False,
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "practice_documentation",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    )

    text = response.output_text
    try:
        return json.loads(text), model
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI returned invalid structured data: {exc}") from exc
