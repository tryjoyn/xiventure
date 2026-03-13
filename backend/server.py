from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone

# SendGrid imports
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str


# Contact Form Models
class ContactFormSubmission(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: EmailStr
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    email_sent: bool = False
    email_error: Optional[str] = None

class ContactFormCreate(BaseModel):
    name: str
    email: EmailStr
    message: str

class ContactFormResponse(BaseModel):
    status: str
    message: str
    id: Optional[str] = None


# Email Service
def send_contact_notification_email(name: str, email: str, message: str, submission_id: str):
    """
    Send contact form notification via SendGrid
    """
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    sender_email = os.environ.get('SENDER_EMAIL', 'noreply@xi.ventures')
    recipient_email = os.environ.get('CONTACT_EMAIL', 'ping@xi.ventures')
    
    if not sendgrid_api_key:
        raise Exception("SendGrid API key not configured")
    
    subject = f"XI Ventures Contact Form: New message from {name}"
    
    html_content = f"""
    <html>
        <body style="font-family: 'Barlow', Arial, sans-serif; background-color: #0a0a0a; color: #ffffff; padding: 40px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #0d0d0d; border: 1px solid rgba(255,255,255,0.1); padding: 40px;">
                <h2 style="color: #c09e53; font-size: 24px; margin-bottom: 30px; text-transform: uppercase; letter-spacing: 0.1em;">
                    New Contact Form Submission
                </h2>
                
                <div style="margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid rgba(255,255,255,0.1);">
                    <p style="color: rgba(255,255,255,0.6); font-size: 12px; text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 8px;">Name</p>
                    <p style="color: #ffffff; font-size: 16px; margin: 0;">{name}</p>
                </div>
                
                <div style="margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid rgba(255,255,255,0.1);">
                    <p style="color: rgba(255,255,255,0.6); font-size: 12px; text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 8px;">Email</p>
                    <p style="color: #ffffff; font-size: 16px; margin: 0;"><a href="mailto:{email}" style="color: #c09e53; text-decoration: none;">{email}</a></p>
                </div>
                
                <div style="margin-bottom: 24px;">
                    <p style="color: rgba(255,255,255,0.6); font-size: 12px; text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 8px;">Message</p>
                    <p style="color: rgba(255,255,255,0.92); font-size: 15px; line-height: 1.7; margin: 0; white-space: pre-wrap;">{message}</p>
                </div>
                
                <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.1);">
                    <p style="color: rgba(255,255,255,0.5); font-size: 12px; margin: 0;">
                        Submission ID: {submission_id}<br>
                        Submitted via xi.ventures contact form
                    </p>
                </div>
            </div>
        </body>
    </html>
    """
    
    mail = Mail(
        from_email=sender_email,
        to_emails=recipient_email,
        subject=subject,
        html_content=html_content
    )
    
    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(mail)
        return response.status_code == 202
    except Exception as e:
        logging.error(f"Failed to send email: {str(e)}")
        raise


async def update_email_status(submission_id: str, success: bool, error: Optional[str] = None):
    """Update the email status in the database"""
    await db.contact_submissions.update_one(
        {"id": submission_id},
        {"$set": {"email_sent": success, "email_error": error}}
    )


async def send_email_task(name: str, email: str, message: str, submission_id: str):
    """Background task to send email and update status"""
    try:
        send_contact_notification_email(name, email, message, submission_id)
        await update_email_status(submission_id, True)
        logging.info(f"Contact notification email sent successfully for submission {submission_id}")
    except Exception as e:
        error_msg = str(e)
        await update_email_status(submission_id, False, error_msg)
        logging.error(f"Failed to send contact notification email: {error_msg}")


# Routes
@api_router.get("/")
async def root():
    return {"message": "XI Ventures API"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks


# Contact Form Routes
@api_router.post("/contact", response_model=ContactFormResponse)
async def submit_contact_form(input: ContactFormCreate, background_tasks: BackgroundTasks):
    """
    Handle contact form submission:
    1. Store in MongoDB
    2. Send email notification via SendGrid (background)
    """
    try:
        # Create submission object
        submission = ContactFormSubmission(
            name=input.name,
            email=input.email,
            message=input.message
        )
        
        # Prepare document for MongoDB
        doc = submission.model_dump()
        doc['timestamp'] = doc['timestamp'].isoformat()
        
        # Store in database
        await db.contact_submissions.insert_one(doc)
        logging.info(f"Contact form submission stored: {submission.id}")
        
        # Send email in background
        background_tasks.add_task(
            send_email_task,
            input.name,
            input.email,
            input.message,
            submission.id
        )
        
        return ContactFormResponse(
            status="success",
            message="Thank you for your message. We'll be in touch soon.",
            id=submission.id
        )
        
    except Exception as e:
        logging.error(f"Failed to process contact form: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to submit contact form")


@api_router.get("/contact/submissions", response_model=List[ContactFormSubmission])
async def get_contact_submissions():
    """Get all contact form submissions (admin endpoint)"""
    submissions = await db.contact_submissions.find({}, {"_id": 0}).to_list(1000)
    
    for sub in submissions:
        if isinstance(sub['timestamp'], str):
            sub['timestamp'] = datetime.fromisoformat(sub['timestamp'])
    
    return submissions


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
