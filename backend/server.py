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

# Emergent LLM imports
from emergentintegrations.llm.chat import LlmChat, UserMessage

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


# Contact Form Models (legacy - kept for backward compatibility)
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


# Ambient Chat Models
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ConversationSession(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    messages: List[dict] = []
    email_captured: Optional[str] = None
    name_captured: Optional[str] = None
    intent: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    response: str
    session_id: str
    action: Optional[str] = None  # "capture_email", "book_call", None
    intent: Optional[str] = None


# XI Ventures System Prompt
XI_SYSTEM_PROMPT = """You are XI Intelligence, the AI assistant for XI Ventures (Extended Intelligence Ventures). 

About XI Ventures:
- We believe human expertise should scale itself through AI
- We back founders building the future of work, where AI amplifies human potential
- We invest in AI-native companies transforming how knowledge workers operate

Your personality:
- Warm but concise - never verbose
- Intellectually curious
- Speak like a thoughtful VC partner, not a corporate bot
- Use "we" when referring to XI Ventures

Your goals:
1. Understand what the visitor is looking for
2. Provide helpful, relevant responses
3. When appropriate, offer to connect them with the team

Response guidelines:
- Keep responses under 3 sentences unless more detail is needed
- Be conversational, not formal
- If someone wants to connect, ask for their email naturally
- If they mention investing, partnerships, or collaboration - they're high-intent
- For general questions about XI, answer directly from the context above

When you detect the user wants to connect or share contact info, end your response with one of these exact phrases:
- If they should share email: [ACTION:CAPTURE_EMAIL]
- If they should book a call: [ACTION:BOOK_CALL]

Never show these action tags to the user in your visible response - they are for system use only.
The action tag should be at the very end, after your conversational response."""


# Email Service
def send_contact_notification_email(name: str, email: str, message: str, submission_id: str):
    """Send contact form notification via SendGrid"""
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    sender_email = os.environ.get('SENDER_EMAIL', 'noreply@xi.ventures')
    recipient_email = os.environ.get('CONTACT_EMAIL', 'ping@xi.ventures')
    
    if not sendgrid_api_key:
        raise Exception("SendGrid API key not configured")
    
    subject = f"XI Ventures: New conversation from {name}"
    
    html_content = f"""
    <html>
        <body style="font-family: 'Barlow', Arial, sans-serif; background-color: #0a0a0a; color: #ffffff; padding: 40px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #0d0d0d; border: 1px solid rgba(255,255,255,0.1); padding: 40px;">
                <h2 style="color: #c09e53; font-size: 24px; margin-bottom: 30px; text-transform: uppercase; letter-spacing: 0.1em;">
                    New Contact via XI Intelligence
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
                    <p style="color: rgba(255,255,255,0.6); font-size: 12px; text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 8px;">Conversation Context</p>
                    <p style="color: rgba(255,255,255,0.92); font-size: 15px; line-height: 1.7; margin: 0; white-space: pre-wrap;">{message}</p>
                </div>
                
                <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.1);">
                    <p style="color: rgba(255,255,255,0.5); font-size: 12px; margin: 0;">
                        Session ID: {submission_id}<br>
                        Captured via XI Intelligence ambient interface
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


async def send_email_background(name: str, email: str, context: str, session_id: str):
    """Background task to send email notification"""
    try:
        send_contact_notification_email(name, email, context, session_id)
        logging.info(f"Email notification sent for session {session_id}")
    except Exception as e:
        logging.error(f"Failed to send email for session {session_id}: {str(e)}")


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


# Ambient Chat Routes
@api_router.post("/chat", response_model=ChatResponse)
async def chat_with_xi(request: ChatRequest, background_tasks: BackgroundTasks):
    """
    Main chat endpoint for XI Intelligence ambient interface
    """
    try:
        # Get or create conversation session
        session = await db.conversations.find_one(
            {"session_id": request.session_id},
            {"_id": 0}
        )
        
        if not session:
            session = {
                "id": str(uuid.uuid4()),
                "session_id": request.session_id,
                "messages": [],
                "email_captured": None,
                "name_captured": None,
                "intent": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.conversations.insert_one(session)
        
        # Add user message to history
        user_msg = {
            "role": "user",
            "content": request.message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Initialize LLM chat
        llm_key = os.environ.get('EMERGENT_LLM_KEY')
        if not llm_key:
            raise HTTPException(status_code=500, detail="LLM key not configured")
        
        chat = LlmChat(
            api_key=llm_key,
            session_id=request.session_id,
            system_message=XI_SYSTEM_PROMPT
        ).with_model("openai", "gpt-4o-mini")
        
        # Build conversation context for the LLM
        # Include recent messages for context
        context_messages = session.get("messages", [])[-6:]  # Last 6 messages for context
        
        # Create context string
        conversation_context = ""
        for msg in context_messages:
            role = "User" if msg["role"] == "user" else "XI"
            conversation_context += f"{role}: {msg['content']}\n"
        
        # Add current message
        full_prompt = f"{conversation_context}User: {request.message}" if conversation_context else request.message
        
        # Send message to LLM
        user_message = UserMessage(text=full_prompt)
        response_text = await chat.send_message(user_message)
        
        # Parse action from response
        action = None
        clean_response = response_text
        
        if "[ACTION:CAPTURE_EMAIL]" in response_text:
            action = "capture_email"
            clean_response = response_text.replace("[ACTION:CAPTURE_EMAIL]", "").strip()
        elif "[ACTION:BOOK_CALL]" in response_text:
            action = "book_call"
            clean_response = response_text.replace("[ACTION:BOOK_CALL]", "").strip()
        
        # Add assistant message to history
        assistant_msg = {
            "role": "assistant",
            "content": clean_response,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Update session in database
        await db.conversations.update_one(
            {"session_id": request.session_id},
            {
                "$push": {"messages": {"$each": [user_msg, assistant_msg]}},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
            }
        )
        
        return ChatResponse(
            response=clean_response,
            session_id=request.session_id,
            action=action
        )
        
    except Exception as e:
        logging.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process message: {str(e)}")


class EmailCaptureRequest(BaseModel):
    session_id: str
    email: EmailStr
    name: Optional[str] = None

@api_router.post("/chat/capture-email")
async def capture_email(request: EmailCaptureRequest, background_tasks: BackgroundTasks):
    """
    Capture email from conversation and send notification
    """
    try:
        # Get conversation session
        session = await db.conversations.find_one(
            {"session_id": request.session_id},
            {"_id": 0}
        )
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Update session with captured email
        await db.conversations.update_one(
            {"session_id": request.session_id},
            {
                "$set": {
                    "email_captured": request.email,
                    "name_captured": request.name,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        # Build conversation context for email
        messages = session.get("messages", [])
        context = "\n".join([
            f"{'User' if m['role'] == 'user' else 'XI'}: {m['content']}"
            for m in messages[-10:]  # Last 10 messages
        ])
        
        # Send email notification in background
        background_tasks.add_task(
            send_email_background,
            request.name or "Website Visitor",
            request.email,
            context,
            request.session_id
        )
        
        return {"status": "success", "message": "Email captured successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Email capture error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to capture email")


# Legacy Contact Form Routes (kept for backward compatibility)
@api_router.post("/contact", response_model=ContactFormResponse)
async def submit_contact_form(input: ContactFormCreate, background_tasks: BackgroundTasks):
    """Legacy contact form endpoint"""
    try:
        submission = ContactFormSubmission(
            name=input.name,
            email=input.email,
            message=input.message
        )
        
        doc = submission.model_dump()
        doc['timestamp'] = doc['timestamp'].isoformat()
        
        await db.contact_submissions.insert_one(doc)
        
        return ContactFormResponse(
            status="success",
            message="Thank you for your message. We'll be in touch soon.",
            id=submission.id
        )
        
    except Exception as e:
        logging.error(f"Failed to process contact form: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to submit contact form")


@api_router.get("/conversations")
async def get_conversations():
    """Get all conversation sessions (admin endpoint)"""
    conversations = await db.conversations.find({}, {"_id": 0}).to_list(1000)
    return conversations


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
