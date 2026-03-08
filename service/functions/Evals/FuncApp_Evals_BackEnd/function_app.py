import azure.functions as func
from telemetry.telemetry_api import telemetry_bp

# Initialize the Function App
app = func.FunctionApp()

app.register_functions(telemetry_bp)

