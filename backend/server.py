from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks, Request
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
import time
from collections import defaultdict
import httpx

# SendGrid imports
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# OpenAI imports
from openai import AsyncOpenAI

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
db_client = AsyncIOMotorClient(mongo_url)
db = db_client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════════════
# PROTECTION CONSTANTS
# ═══════════════════════════════════════════════════
MAX_MESSAGES_PER_SESSION = 5
MAX_MESSAGES_PER_IP_HOUR = 20
COOLDOWN_SECONDS = 3
MAX_MESSAGE_LENGTH = 500
GEO_CACHE_TTL = 3600  # 1 hour

# In-memory rate limiting stores
session_msg_counts = defaultdict(int)
ip_timestamps = defaultdict(list)
last_message_time = defaultdict(float)

# Geo-IP cache
geo_cache = {}

# Known bot User-Agent patterns
BOT_UA_PATTERNS = [
    "bot", "crawler", "spider", "scraper", "wget",
    "python-requests", "go-http-client", "java/",
    "perl", "mechanize", "phantom", "selenium",
    "headless", "puppeteer", "playwright", "scrapy",
    "httpclient", "libwww", "lwp-", "nutch", "archive",
    "slurp", "mediapartners", "feedfetcher"
]


# ═══════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════
def get_client_ip(request: Request) -> str:
    """Extract real client IP from proxy headers"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def is_bot_user_agent(user_agent: str) -> bool:
    """Check if User-Agent matches known bot patterns"""
    if not user_agent or len(user_agent) < 10:
        return True
    ua_lower = user_agent.lower()
    return any(pattern in ua_lower for pattern in BOT_UA_PATTERNS)


async def get_country_code(ip: str) -> str:
    """Get country code from IP using free geo-IP API with caching"""
    if ip in ("unknown", "127.0.0.1", "localhost", "::1"):
        return "US"  # Treat local as US for dev

    now = time.time()
    cached = geo_cache.get(ip)
    if cached and cached["expires"] > now:
        return cached["country_code"]

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode")
            data = resp.json()
            if data.get("status") == "success":
                cc = data.get("countryCode", "XX")
                geo_cache[ip] = {"country_code": cc, "expires": now + GEO_CACHE_TTL}
                return cc
    except Exception as e:
        logging.warning(f"Geo-IP lookup failed for {ip}: {e}")

    return "XX"  # Unknown — will be blocked (US-only policy)


async def verify_turnstile(token: str, ip: str) -> bool:
    """Verify Cloudflare Turnstile CAPTCHA token"""
    secret = os.environ.get('TURNSTILE_SECRET_KEY')
    if not secret:
        return True  # Skip verification if not configured

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": secret, "response": token, "remoteip": ip}
            )
            result = resp.json()
            return result.get("success", False)
    except Exception as e:
        logging.warning(f"Turnstile verification failed: {e}")
        return True  # Fail open if Turnstile is down


def check_rate_limits(session_id: str, ip: str) -> Optional[str]:
    """Check all rate limits. Returns error reason or None if OK."""
    now = time.time()

    # Session message limit
    if session_msg_counts[session_id] >= MAX_MESSAGES_PER_SESSION:
        return "session_limit"

    # Cooldown between messages
    if now - last_message_time[session_id] < COOLDOWN_SECONDS:
        return "cooldown"

    # IP hourly limit
    hour_ago = now - 3600
    ip_timestamps[ip] = [t for t in ip_timestamps[ip] if t > hour_ago]
    if len(ip_timestamps[ip]) >= MAX_MESSAGES_PER_IP_HOUR:
        return "ip_limit"

    return None


def record_message(session_id: str, ip: str):
    """Record a message for rate limiting"""
    session_msg_counts[session_id] += 1
    ip_timestamps[ip].append(time.time())
    last_message_time[session_id] = time.time()


async def log_chat_event(
    session_id: str, ip: str, country: str, user_agent: str,
    message: str, response: str, blocked: bool,
    block_reason: Optional[str], message_number: int
):
    """Log chat interaction to MongoDB for analytics"""
    log_entry = {
        "session_id": session_id,
        "ip": ip,
        "country": country,
        "user_agent": user_agent[:200] if user_agent else None,
        "message": message[:500] if message else None,
        "response": response[:500] if response else None,
        "blocked": blocked,
        "block_reason": block_reason,
        "message_number": message_number,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    try:
        await db.chat_logs.insert_one(log_entry)
    except Exception as e:
        logging.error(f"Failed to log chat event: {e}")


# ═══════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

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

class ChatMessage(BaseModel):
    role: str
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
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)
    hp_field: Optional[str] = None  # Honeypot — must be empty
    captcha_token: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    action: Optional[str] = None
    intent: Optional[str] = None
    remaining: Optional[int] = None  # Messages remaining in session


# ═══════════════════════════════════════════════════
# XI VENTURES SYSTEM PROMPT
# ═══════════════════════════════════════════════════
XI_SYSTEM_PROMPT = """You are XI Intelligence, the AI assistant for XI Ventures (Extended Intelligence Ventures). 

About XI Ventures:
- XI Ventures is a venture studio building the next generation of business operating systems
- Our mission: scaling human potential and expertise through intelligent systems, emphasizing collaboration between human intelligence and AI
- We believe human expertise should amplify itself, not be replaced
- We're building at the intersection of AI and human intelligence

Your personality:
- Warm but concise - never verbose
- Intellectually curious
- Speak like a thoughtful team member, not a corporate bot
- Use "we" when referring to XI Ventures

CRITICAL RULES:
1. You do NOT know specifics about our product, timeline, funding, team size, or technical details
2. When asked about specifics you don't know, redirect: "I'd love to share more - let's connect directly!"
3. NEVER make up facts, numbers, or details about XI Ventures
4. You are NOT an investment firm - do not discuss investments, portfolio, or funding rounds
5. Your PRIMARY GOAL is to guide visitors toward sharing their contact info or booking a call

What you CAN discuss:
- Our general mission (scaling human potential through AI)
- Our belief in human-AI collaboration
- That we're a venture studio building something meaningful
- That we're always excited to connect with like-minded people

What you should REDIRECT to human connection:
- Specific product details -> "Let's connect and I can share more"
- Funding/investment questions -> "We're focused on building right now. Happy to chat directly!"
- Team details -> "Our team would love to introduce themselves"
- Technical specifics -> "Great question - that deserves a proper conversation"

Response guidelines:
- Keep responses to 2-3 sentences MAX
- Be genuinely curious about the visitor
- After the FIRST exchange, ALWAYS try to move toward capturing their email or booking a call
- Use natural prompts like "I'd love to keep this conversation going - what's the best email to reach you?"

IMPORTANT: Add [ACTION:CAPTURE_EMAIL] at the very end of your message (after the period). This is required for the UI to show contact options. Never skip this after the first exchange."""


# ═══════════════════════════════════════════════════
# EMAIL SERVICE
# ═══════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════
@api_router.get("/")
async def root():
    return {"message": "XI Ventures API"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    return status_checks


# ═══════════════════════════════════════════════════
# AMBIENT CHAT ROUTES (PROTECTED)
# ═══════════════════════════════════════════════════
@api_router.post("/chat", response_model=ChatResponse)
async def chat_with_xi(chat_request: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """Main chat endpoint with abuse protection"""
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    country = "XX"
    blocked = False
    block_reason = None
    response_text = ""
    msg_number = session_msg_counts.get(chat_request.session_id, 0) + 1

    try:
        # ── Protection Layer 1: Honeypot ──
        if chat_request.hp_field:
            blocked = True
            block_reason = "honeypot"
            await log_chat_event(
                chat_request.session_id, client_ip, country, user_agent,
                chat_request.message, "", True, "honeypot", msg_number
            )
            # Return fake success to not tip off bots
            return ChatResponse(
                response="Thanks for reaching out! We'd love to connect. Email us at ping@xi.ventures.",
                session_id=chat_request.session_id,
                remaining=0
            )

        # ── Protection Layer 2: Bot Detection ──
        if is_bot_user_agent(user_agent):
            blocked = True
            block_reason = "bot"
            await log_chat_event(
                chat_request.session_id, client_ip, country, user_agent,
                chat_request.message, "", True, "bot", msg_number
            )
            return ChatResponse(
                response="Thanks for reaching out! We'd love to connect. Email us at ping@xi.ventures.",
                session_id=chat_request.session_id,
                remaining=0
            )

        # ── Protection Layer 3: Geo-restriction (US only) ──
        country = await get_country_code(client_ip)
        if country != "US":
            blocked = True
            block_reason = "geo"
            await log_chat_event(
                chat_request.session_id, client_ip, country, user_agent,
                chat_request.message, "", True, "geo", msg_number
            )
            return ChatResponse(
                response="Thanks for your interest in XI Ventures! Our AI chat is currently available in the US only. We'd love to hear from you — please reach out directly at ping@xi.ventures and we'll get back to you promptly.",
                session_id=chat_request.session_id,
                remaining=0
            )

        # ── Protection Layer 4: Rate Limiting ──
        limit_reason = check_rate_limits(chat_request.session_id, client_ip)
        if limit_reason:
            blocked = True
            block_reason = limit_reason
            await log_chat_event(
                chat_request.session_id, client_ip, country, user_agent,
                chat_request.message, "", True, limit_reason, msg_number
            )

            if limit_reason == "session_limit":
                return ChatResponse(
                    response="You've reached the conversation limit. We'd love to continue — email us at ping@xi.ventures or book a call!",
                    session_id=chat_request.session_id,
                    action="capture_email",
                    remaining=0
                )
            elif limit_reason == "cooldown":
                raise HTTPException(status_code=429, detail="Please wait a moment before sending another message.")
            else:
                raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")

        # ── Protection Layer 5: CAPTCHA (Cloudflare Turnstile) ──
        if os.environ.get('TURNSTILE_SECRET_KEY'):
            token = chat_request.captcha_token
            if not token:
                raise HTTPException(status_code=400, detail="CAPTCHA verification required.")
            if not await verify_turnstile(token, client_ip):
                await log_chat_event(
                    chat_request.session_id, client_ip, country, user_agent,
                    chat_request.message, "", True, "captcha_fail", msg_number
                )
                raise HTTPException(status_code=403, detail="CAPTCHA verification failed.")

        # ── Protection Layer 6: Message length ──
        if len(chat_request.message.strip()) == 0:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")

        # ══════════════════════════════════════════
        # ALL CHECKS PASSED — Process the message
        # ══════════════════════════════════════════

        # Get or create conversation session
        session = await db.conversations.find_one(
            {"session_id": chat_request.session_id},
            {"_id": 0}
        )

        if not session:
            session = {
                "id": str(uuid.uuid4()),
                "session_id": chat_request.session_id,
                "messages": [],
                "email_captured": None,
                "name_captured": None,
                "intent": None,
                "ip": client_ip,
                "country": country,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.conversations.insert_one(session)

        # Add user message
        user_msg = {
            "role": "user",
            "content": chat_request.message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Call OpenAI
        openai_key = os.environ.get('OPENAI_API_KEY')
        if not openai_key:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        openai_client = AsyncOpenAI(api_key=openai_key)

        context_messages = session.get("messages", [])[-6:]
        messages = [{"role": "system", "content": XI_SYSTEM_PROMPT}]
        for msg in context_messages:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": chat_request.message})

        completion = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0.7
        )

        response_text = completion.choices[0].message.content

        # Parse action
        action = None
        clean_response = response_text
        if "[ACTION:CAPTURE_EMAIL]" in response_text:
            action = "capture_email"
            clean_response = response_text.replace("[ACTION:CAPTURE_EMAIL]", "").strip()
        elif "[ACTION:BOOK_CALL]" in response_text:
            action = "book_call"
            clean_response = response_text.replace("[ACTION:BOOK_CALL]", "").strip()

        # Save to conversation
        assistant_msg = {
            "role": "assistant",
            "content": clean_response,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await db.conversations.update_one(
            {"session_id": chat_request.session_id},
            {
                "$push": {"messages": {"$each": [user_msg, assistant_msg]}},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
            }
        )

        # Record for rate limiting
        record_message(chat_request.session_id, client_ip)
        remaining = MAX_MESSAGES_PER_SESSION - session_msg_counts[chat_request.session_id]

        # Log successful interaction
        await log_chat_event(
            chat_request.session_id, client_ip, country, user_agent,
            chat_request.message, clean_response, False, None, msg_number
        )

        return ChatResponse(
            response=clean_response,
            session_id=chat_request.session_id,
            action=action,
            remaining=max(remaining, 0)
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process message: {str(e)}")


class EmailCaptureRequest(BaseModel):
    session_id: str
    email: EmailStr
    name: Optional[str] = None

@api_router.post("/chat/capture-email")
async def capture_email(request: EmailCaptureRequest, background_tasks: BackgroundTasks):
    """Capture email from conversation and send notification"""
    try:
        session = await db.conversations.find_one(
            {"session_id": request.session_id},
            {"_id": 0}
        )

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

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

        messages = session.get("messages", [])
        context = "\n".join([
            f"{'User' if m['role'] == 'user' else 'XI'}: {m['content']}"
            for m in messages[-10:]
        ])

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


# ═══════════════════════════════════════════════════
# ADMIN / ANALYTICS ROUTES
# ═══════════════════════════════════════════════════
@api_router.get("/conversations")
async def get_conversations():
    """Get all conversation sessions"""
    conversations = await db.conversations.find({}, {"_id": 0}).to_list(1000)
    return conversations

@api_router.get("/chat-logs")
async def get_chat_logs(limit: int = 100, blocked_only: bool = False):
    """Get chat logs for analytics and abuse monitoring"""
    query = {"blocked": True} if blocked_only else {}
    logs = await db.chat_logs.find(query, {"_id": 0}).sort("timestamp", -1).to_list(limit)
    return logs

@api_router.get("/chat-stats")
async def get_chat_stats():
    """Get aggregate chat statistics"""
    try:
        total_messages = await db.chat_logs.count_documents({})
        blocked_messages = await db.chat_logs.count_documents({"blocked": True})
        unique_sessions = len(await db.chat_logs.distinct("session_id"))
        unique_ips = len(await db.chat_logs.distinct("ip"))

        block_reasons = {}
        for reason in ["geo", "bot", "honeypot", "session_limit", "ip_limit", "cooldown", "captcha_fail"]:
            count = await db.chat_logs.count_documents({"block_reason": reason})
            if count > 0:
                block_reasons[reason] = count

        # Country breakdown
        pipeline = [
            {"$group": {"_id": "$country", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        country_stats = await db.chat_logs.aggregate(pipeline).to_list(10)

        return {
            "total_messages": total_messages,
            "blocked_messages": blocked_messages,
            "allowed_messages": total_messages - blocked_messages,
            "unique_sessions": unique_sessions,
            "unique_ips": unique_ips,
            "block_reasons": block_reasons,
            "top_countries": {s["_id"]: s["count"] for s in country_stats if s["_id"]}
        }
    except Exception as e:
        logging.error(f"Stats error: {str(e)}")
        return {"error": str(e)}


# Legacy Contact Form Routes
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


# Include the router
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
    db_client.close()
