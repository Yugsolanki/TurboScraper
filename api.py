from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uuid
import asyncio
import logging
from api_scraper import ScapeJob, ScrapeRequest, ScrapeResponse, ScraperConfig
from api_scraper import AsyncParallelWebScraper

logger = logging.getLogger(__name__)

# Global variables to track the scraper job
scraper = None
current_job_id = None
job_status = None
job_results = None
scrape_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API starting up")
    yield
    logger.info("API shutting down")
    # Ensure we clean up the scraper on shutdown
    if scraper:
        await scraper.close()

app = FastAPI(
    title="Async Parallel Web Scraper API",
    description="An asynchronous web scraper API built with FastAPI and Playwright.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


async def scrape_website_task(config: ScapeJob):
    """
    The actual scraping task that runs in the background.
    Updates global variables with status and results.
    """
    global scraper, job_status, job_results

    job_status = "running"
    try:
        await scraper.initialize()
        logger.info("Scraper initialized successfully")

        # Start scraping
        scraped_links, external_links = await scraper.scrape_website(
            request=ScrapeRequest(
                start_url=str(config.start_url),
                whitelisted_domains=config.whitelisted_domains,
                blacklisted_domains=config.blacklisted_domains,
                blacklisted_paths=config.blacklisted_paths,
                whitelisted_paths=config.whitelisted_paths
            )
        )

        # Store results
        job_results = {
            "scraped_urls": list(scraped_links),
            "external_links": list(external_links),
            "job_id": current_job_id,
            "status": "completed"
        }
        job_status = "completed"
        logger.info(f"Scraping completed: {len(scraped_links)} links scraped.")

    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        job_status = "failed"
        job_results = {
            "error": str(e),
            "job_id": current_job_id,
            "status": "failed"
        }
    finally:
        if scraper:
            await scraper.close()


@app.post("/scrape")
async def scrape(request: ScapeJob):
    """
    Start a new scraping job that runs in the background.
    Returns immediately with a job ID that can be used to check status.
    """
    global scraper, current_job_id, job_status, job_results, scrape_task

    # Check if a job is already running
    if job_status == "running" and scrape_task and not scrape_task.done():
        return JSONResponse(
            content={
                "job_id": current_job_id,
                "status": "already_running",
                "message": "A scraping job is already in progress. Use /status to check progress or /stop to stop it."
            },
            status_code=409  # Conflict status code
        )

    # Clean up any previous task
    if scrape_task and not scrape_task.done():
        scrape_task.cancel()

    # If there's an existing scraper, close it
    if scraper:
        await scraper.close()

    # Create a new scraper instance
    scraper = AsyncParallelWebScraper(
        config=ScraperConfig(
            max_concurrent=request.max_concurrent,
            timeout=request.timeout,
            max_retries=request.max_retries,
            max_depth=request.max_depth,
            rate_limit_delay=request.rate_limit_delay,
            respect_robots=request.respect_robots,
            user_agents=request.user_agents
        )
    )

    # Generate a new job ID
    current_job_id = str(uuid.uuid4())
    job_status = "starting"
    job_results = None

    # Start the scraping task in the background
    scrape_task = asyncio.create_task(scrape_website_task(request))

    # Return immediately with the job ID
    return JSONResponse(
        content={
            "job_id": current_job_id,
            "status": "started",
            "message": "Scraping job started"
        },
        status_code=202  # Accepted status code
    )


@app.get("/status")
async def get_status():
    """
    Get the status of the current scraping job.
    If job is completed, returns the results too.
    """
    if job_status is None:
        return JSONResponse(
            content={"status": "no_job", "message": "No job has been started"},
            status_code=200
        )

    response = {"job_id": current_job_id, "status": job_status}

    # Include results if job is completed or failed
    if job_status in ["completed", "failed"] and job_results:
        response.update(job_results)

    return JSONResponse(content=response, status_code=200)


@app.post("/stop")
async def stop():
    """
    Stop the current scraping job if it's running.
    """
    global scraper, job_status, scrape_task

    if job_status != "running" or not scrape_task or scrape_task.done():
        return JSONResponse(
            content={"status": "not_running",
                     "message": "No scraping job is currently running"},
            status_code=200
        )

    # Cancel the scraping task
    if scrape_task:
        scrape_task.cancel()

    # Close the scraper
    if scraper:
        await scraper.close()
        scraper = None

    job_status = "stopped"

    return JSONResponse(
        content={"job_id": current_job_id, "status": "stopped",
                 "message": "Scraping job stopped"},
        status_code=200
    )


@app.get("/results/")
async def get_results(job_id: str = None):
    """
    Get the results of a specific job.
    """
    global current_job_id, job_status, job_results

    if current_job_id is None:
        return JSONResponse(
            content={"error": "No job has been started"},
            status_code=404
        )

    if current_job_id != job_id:
        return JSONResponse(
            content={"error": "Job not found"},
            status_code=404
        )

    if job_status not in ["completed", "failed"] or not job_results:
        return JSONResponse(
            content={"job_id": job_id, "status": job_status,
                     "message": "Results not available yet"},
            status_code=202
        )

    if not job_results:
        return JSONResponse(
            content={"error": "No results available"},
            status_code=404
        )

    return JSONResponse(content=job_results, status_code=200)


@app.get("/")
async def root():
    """
    Root endpoint that returns a welcome message.
    """
    return JSONResponse(
        content={"message": "Welcome to the Async Parallel Web Scraper API!"},
        status_code=200
    )


@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify if the API is running.
    """
    return JSONResponse(
        content={"status": "healthy"},
        status_code=200
    )
