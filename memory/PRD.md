# XI Ventures - Ambient AI Contact Interface

## Original Problem Statement
User wanted to connect to GitHub repository https://github.com/tryjoyn/xiventure (XI Ventures website) and:
1. Add contact form backend integration
2. **Upgraded to**: Futuristic AI-powered ambient interface
3. **Refined**: Added boundaries to prevent hallucination, focus on lead capture

## Architecture
- **Frontend**: Static HTML/CSS/JS (XI Ventures landing page) via React CRA
- **Backend**: FastAPI with MongoDB
- **AI**: GPT-4o-mini via Emergent Universal Key
- **Email**: SendGrid (requires API key configuration)

## About XI Ventures (for AI context)
- **NOT an investment firm** - building, not investing
- Early-stage company building next-gen business operating systems
- Mission: Scaling human potential and expertise through intelligent systems
- Focus: Human-AI collaboration

## AI Boundaries (Critical)
The AI is configured to:
- ✅ Discuss general mission (scaling human potential)
- ✅ Confirm early-stage status
- ✅ Always guide toward contact capture
- ❌ NOT make up specific facts (funding, team size, product details)
- ❌ NOT discuss investments or portfolio
- ❌ NOT pretend to be a VC fund

## What's Been Implemented (March 13, 2026)

### Backend (`/app/backend/server.py`)
- `POST /api/chat` - AI chat with strict boundaries
- `POST /api/chat/capture-email` - Email capture with conversation context
- `GET /api/conversations` - Admin endpoint
- System prompt with XI Ventures brand guidelines

### Frontend (`/app/frontend/public/index.html`)
- Ambient "Tell us *anything.*" interface
- Conversation UI with XI Intelligence branding
- Equal emphasis on "Share Email" and "Book a Call" buttons
- Email capture modal with blur backdrop

### AI Behavior
- Short responses (2-3 sentences)
- Redirects specific questions to human connection
- Triggers contact buttons after first meaningful exchange
- Never fabricates facts

## Environment Variables
```
EMERGENT_LLM_KEY=sk-emergent-... (configured)
SENDGRID_API_KEY= (needs user config)
SENDER_EMAIL=noreply@xi.ventures
CONTACT_EMAIL=ping@xi.ventures
```

## Testing Results
- All 25 tests passing (100%)
- AI boundaries verified manually
- Lead capture flow working

## Backlog

### P0 (Critical)
- [ ] Configure SendGrid API key

### P1 (High Priority)  
- [ ] Rate limiting
- [ ] Spam detection

### P2 (Medium Priority)
- [ ] Calendar booking integration (Calendly/Cal.com)
- [ ] Conversation analytics

## Next Tasks
1. Add SendGrid API key to enable email notifications
2. Consider Calendly integration for "Book a Call"
