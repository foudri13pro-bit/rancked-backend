from fastapi import FastAPI

app = FastAPI(title="Zenavia Ranked API")

@app.get("/")
def read_root():
    return {"message": "Bienvenue sur l'API Zenavia Ranked!"}

@app.get("/healthz")
def health():
    return {"status": "ok"}
