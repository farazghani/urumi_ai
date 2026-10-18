def main() -> None:
    import uvicorn
    uvicorn.run('backend.app:app', host='127.0.0.1', port=8001)
