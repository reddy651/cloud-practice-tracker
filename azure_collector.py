from app import create_app
from app.services.azure_collector import run_loop

app = create_app()
run_loop(app)
