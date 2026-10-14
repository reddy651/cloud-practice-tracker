# Cloud Practice Tracker V4 — Screenshots + AI + Azure Activity Log → Blob/S3 → Website

This version is designed for a user who does not want to type practice explanations manually.

## Workflow

1. Create a practice.
2. Paste screenshots with `Ctrl+V` or select image files.
3. For an existing practice, pasted screenshots can auto-submit from the screenshot box; they are saved with sequence numbers such as `001_...png`, `002_...png`.
4. Click **Analyze All Screenshots with AI** after you have collected the screenshots you want the AI to read.
5. OpenAI vision reads all screenshots in order and fills title, provider, service, topic, description, learning notes, problems, solutions, commands, and interview questions.
6. Edit anything later from **Edit Practice**.
7. Generate the Markdown document and PDF.
8. With `STORAGE_BACKEND=both`, screenshots, Markdown, PDF, and raw Azure Activity Log JSON batches are written to both Azure Blob Storage and AWS S3.
9. The Flask app controls access to each user's records; storage objects should not be made public.
10. A separate Azure collector process polls Azure Activity Log automatically (default every 5 minutes), saves new events to MySQL, archives the raw event batch to Blob/S3, and the website displays the events under the practice.

## Important

- You need an **OpenAI API key**. A ChatGPT subscription is separate from API billing.
- Never put the OpenAI API key in browser JavaScript. Keep it in the server `.env` file.
- The application sends the screenshots to the OpenAI API when you click the AI button.
- The AI is instructed not to invent unseen steps and to redact obvious secrets. Review generated documentation before relying on it.
- `store=False` is used for Responses API requests in this project.
- Configure Azure Blob, AWS S3, or both.

## Environment

Copy `.env.example` to `.env` and configure at minimum:

```text
SECRET_KEY=replace-me
DATABASE_URL=mysql+mysqlconnector://az_user:YOUR_PASSWORD@localhost:3306/practice_tracker
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.6-luna
STORAGE_BACKEND=both
```

### Azure Blob

```text
AZURE_STORAGE_CONNECTION_STRING=...
AZURE_STORAGE_CONTAINER=practice-files
```

Or use `AZURE_STORAGE_ACCOUNT_URL` with `DefaultAzureCredential`.

### AWS S3

```text
AWS_REGION=ap-south-1
AWS_S3_BUCKET=your-bucket
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Prefer an IAM role or another secure credential provider over long-lived access keys when possible.

## Install on AlmaLinux/RHEL VPS

```bash
cd /root/cloud-practice-tracker
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
python run.py
```

The app runs on port 8000 by default.

## Existing V2 database

On startup the app calls `db.create_all()` and a small schema upgrade helper that adds the new optional columns:

- `practice_sessions.interview_questions`
- `practice_sessions.ai_model`
- `practice_sessions.ai_analyzed_at`
- `screenshots.sequence_no`

Back up your database before upgrading a production installation.

## Storage layout

```text
users/<user_id>/practices/<practice_id>/screenshots/001_name.png
users/<user_id>/practices/<practice_id>/screenshots/002_name.png
users/<user_id>/practices/<practice_id>/practice.md
users/<user_id>/practices/<practice_id>/practice-report.pdf
```

The same object key is used in Azure Blob and S3. The dashboard never trusts a user-supplied storage path; it verifies the practice belongs to the logged-in user before serving files.

## Azure Activity Log

The existing Azure sync remains available for Azure practices. It collects Azure control-plane activity for the practice time range; it does not capture every portal mouse click.

## Automatic Azure Activity Log collection

For Azure practices, the separate `azure_collector.py` process automatically polls the Azure subscription Activity Log. It uses the practice start time and, while the practice is `In Progress`, the current time. After the practice ends it can continue reconciling that practice for 24 hours, which helps catch Activity Log events that arrived a few minutes late. Duplicate events are skipped using event time, operation, correlation ID, and resource ID.

Azure Activity Log is a control-plane log: it records management operations such as create, update, delete and action operations. It does **not** capture every portal click or ordinary read operation. Microsoft states that Activity Log events are typically available within 3–20 minutes and are retained for 90 days by default. citeturn0search0

### VPS authentication

Because this application runs on a normal Linux VPS rather than inside Azure, configure a Microsoft Entra service principal with permission to read Activity Log events at the subscription scope. The permission required for the Activity Log is `Microsoft.Insights/eventtypes/*`; Microsoft documents a custom Activity Log Reader role using that action, and the built-in Reader role also contains it. citeturn0search12

Set:

```text
AZURE_SUBSCRIPTION_ID=...
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_ACTIVITY_POLL_SECONDS=300
```

Then install the collector as a systemd service:

```bash
cp cloud-practice-tracker-azure-collector.service /etc/systemd/system/cloud-practice-tracker-azure-collector.service
systemctl daemon-reload
systemctl enable --now cloud-practice-tracker-azure-collector
systemctl status cloud-practice-tracker-azure-collector
journalctl -u cloud-practice-tracker-azure-collector -f
```

### Cloud archive path

Each newly collected batch is stored under:

```text
users/<user_id>/practices/<practice_id>/azure-activity/activity-YYYYMMDDTHHMMSSZ.json
```

With `STORAGE_BACKEND=both`, the same object key is written to Azure Blob and AWS S3. The website reads the normalized events from MySQL, while the raw JSON remains available in cloud storage.

### Optional Azure-native archival

For longer-term or higher-volume retention, Azure Monitor can also export Activity Log directly to Azure Storage through a Diagnostic Setting. Microsoft documents Azure Storage as an export destination and notes that Activity Log is otherwise retained for 90 days by default. citeturn0search0 This is optional; the collector in this project already archives the events it retrieves.


## Step-by-Step workflow (v6)

- One screenshot = one saved step.
- Each saved step has separate **Save Step** and **Update Document** actions.
- **Update Document** saves the edited explanation and rebuilds the Word document using all steps.
- Final document controls are at the **top** of the practice page.
- **Save Word Document** creates the complete DOCX from every saved step in sequence.
- **Convert to PDF** creates the complete PDF from the same saved sequence.
- Word/PDF order is: Step #001 -> screenshot -> explanation -> Step #002 -> screenshot -> explanation ...
- Exact duplicate screenshots and duplicate explanations are rejected for new steps.
- Final Word/PDF creation is blocked if existing saved steps contain exact duplicate screenshots, duplicate explanations, or missing explanations; remove/fix the offending step first.
- After finalizing, **Edit / Add More Screenshots** reopens the practice so more sequential steps can be added.
