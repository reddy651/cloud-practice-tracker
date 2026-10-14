import json
import re
from datetime import datetime

from .ai_assistant import _gemini_request


def _extract_json(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise RuntimeError('Gemini did not return valid JSON.')


def generate_practice_details(practice, step_context=''):
    provider = practice.provider or ''
    title = practice.title or ''
    service = practice.service or ''
    topic = practice.topic or ''
    description = practice.description or ''
    prompt = f"""You are generating study documentation for an IT hands-on practice record.

Existing metadata (use it as the starting point; do not invent a different technology):
Provider: {provider}
Title: {title}
Service: {service}
Topic: {topic}
Description/context: {description}

The user saved these exact step explanations from their hands-on work:
{step_context or 'No step explanations are available.'}

Generate a concise but useful study record from the evidence above. The user wants the Edit Practice page fields populated automatically.

IMPORTANT:
- Keep the provider aligned with the existing provider unless the evidence clearly proves otherwise.
- Base the service/topic/description on the actual practice and saved steps.
- Do not claim commands, problems, or solutions that are not reasonably supported by the steps. If there is no evidence of a problem, say "No specific problem recorded during this practice.".
- Commands / Technical Notes should contain only commands or concrete technical configuration notes that are supported by the steps; otherwise say "No commands recorded.".
- What I Practiced / Learned should summarize the hands-on skills actually demonstrated.
- Generate EXACTLY 25 interview questions AND exactly 25 matching answers.
- Mix conceptual, practical and troubleshooting questions, with at least 15 clearly scenario-based questions.
- Questions should be specific to the technology/topic in this practice, not generic interview questions.
- Each answer must directly answer the question immediately above it and be technically useful for interview preparation.
- Keep answers concise but sufficient: normally 2-5 sentences, with commands/configuration examples only when supported by the practice/topic.
- Do not invent details about the user's actual environment. The answers are study/reference answers, not claims that the user performed every action.
- Return ONLY valid JSON matching the requested keys.

JSON keys:
"title", "provider", "service", "topic", "description", "notes", "problems", "solutions", "commands", "interview_questions", "interview_answers"

For interview_questions, return one string containing 25 numbered questions, using this format:
1. ...
2. ...
...
25. ...

For interview_answers, return one string containing 25 numbered answers in the SAME order. Each number must correspond to the same-numbered question:
1. ...
2. ...
...
25. ...
"""
    text, model = _gemini_request([{"role": "user", "parts": [{"text": prompt}]}])
    data = _extract_json(text)
    keys = ["title", "provider", "service", "topic", "description", "notes", "problems", "solutions", "commands", "interview_questions", "interview_answers"]
    result = {k: str(data.get(k, '') or '').strip() for k in keys}

    # Gemini may return HTML line breaks even though the prompt asks for
    # numbered plain-text lines. Normalize those breaks before validation and
    # storage so questions/answers are always displayed one-per-line in order.
    def normalize_numbered_text(value):
        value = re.sub(r'(?i)<br\s*/?>', '\n', value)
        value = value.replace('\r\n', '\n').replace('\r', '\n')
        # Some responses use multiple blank lines; keep the document compact.
        lines = [line.strip() for line in value.split('\n') if line.strip()]
        # If the model placed several numbered entries on one physical line,
        # split at each numbered marker as a fallback.
        if len(lines) == 1:
            parts = re.split(r'(?=\b\d+\.\s+)', lines[0])
            lines = [part.strip() for part in parts if part.strip()]
        normalized = []
        for idx, line in enumerate(lines, 1):
            line = re.sub(r'^\s*\d+\.\s*', '', line).strip()
            if line:
                normalized.append(f'{idx}. {line}')
        return '\n'.join(normalized)

    result['interview_questions'] = normalize_numbered_text(result['interview_questions'])
    result['interview_answers'] = normalize_numbered_text(result['interview_answers'])

    if not result['interview_questions']:
        raise RuntimeError('Gemini returned no interview questions.')
    if not result['interview_answers']:
        raise RuntimeError('Gemini returned no interview answers.')
    # Basic guard: if the model ignored the exact count, reject instead of silently saving bad data.
    numbered = re.findall(r'(?m)^\s*(\d+)\.\s+', result['interview_questions'])
    if len(numbered) != 25:
        raise RuntimeError(f'Gemini returned {len(numbered)} numbered interview questions instead of 25.')
    answer_numbered = re.findall(r'(?m)^\s*(\d+)\.\s+', result['interview_answers'])
    if len(answer_numbered) != 25:
        raise RuntimeError(f'Gemini returned {len(answer_numbered)} numbered interview answers instead of 25.')
    return result, model, datetime.now()
