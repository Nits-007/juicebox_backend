from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from juicebox_scraper import run_scraper
import asyncio
import os
import sys
import io
import csv

def _run_scraper_sync(query: str, fetch_limit: int, headless: bool):
    """Run the async scraper in a fresh event loop on Windows to avoid NotImplementedError."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            run_scraper(query=query, fetch_limit=fetch_limit, headless=headless, save_to_disk=False)
        )
    finally:
        loop.close()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScrapeRequest(BaseModel):
    query: str
    fetch_limit: int = 100
    headless: bool = True


@app.get("/")
async def root():
    return {
        "message": "Juicebox Scraper API Running"
    }


@app.post("/scrape")
async def scrape(data: ScrapeRequest):

    try:
        results, filepath, error = await asyncio.to_thread(
            _run_scraper_sync,
            data.query,
            data.fetch_limit,
            data.headless,
        )

        if error:
            raise HTTPException(
                status_code=500,
                detail=error
            )

        csv_content = ""
        if results:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["name", "city", "state", "full_location"])
            writer.writeheader()
            writer.writerows(results)
            csv_content = output.getvalue()

        return {
            "success": True,
            "total_records": len(results),
            "csv_content": csv_content,
            "data": results
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
