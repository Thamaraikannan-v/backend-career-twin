# Career Twin Backend

**AI-powered recruiter simulation that helps you see yourself the way recruiters do.**

A FastAPI-based backend service that analyzes resumes, job descriptions, company information, and generates personalized cold emails and career advice using AI agents.

## 🎯 Overview

Career Twin is an AI platform that provides:
- **Resume Analysis** - AI-powered resume critique from a recruiter's perspective
- **Job Matching** - Intelligent job search and matching based on your profile
- **Cold Email Generation** - Personalized outreach emails to recruiters and companies
- **Career Guidance** - AI advisor for career development strategies
- **Company Intelligence** - Research and analysis of target companies
- **Billing & Analytics** - Stripe integration with usage tracking

## 🚀 Features

- **Multi-Agent Architecture** - Specialized AI agents for different tasks (resume, job search, recruiter, company analysis)
- **LangGraph Integration** - Stateful AI workflows and reasoning chains
- **Async FastAPI** - High-performance async request handling
- **Supabase Integration** - PostgreSQL database with real-time capabilities
- **Stripe Payments** - Pro tier billing and subscription management
- **Email Service** - Hunter.io integration for recruiter discovery
- **Web Search** - Tavily API for company research
- **Semantic Search** - Exa for intelligent job discovery

## 📋 Prerequisites

- Python 3.11+
- PostgreSQL (via Supabase)
- API Keys:
  - Groq or Gemini (LLM)
  - Supabase (Database)
  - Stripe (Payments)
  - Hunter.io (Email finder)
  - Tavily (Web search)
  - Exa (Job search)

## 🔧 Local Setup

### 1. Clone & Install

```bash
git clone https://github.com/yourusername/career-twin.git
cd backend-career-twin

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Variables

Create a `.env` file:

```env
# LLM Providers
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
LLM_PROVIDER=groq  # or "gemini"

# Database
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=your_service_key
SUPABASE_JWT_SECRET=your_jwt_secret

# APIs
TAVILY_API_KEY=your_tavily_key
HUNTER_API_KEY=your_hunter_key
EXA_API_KEY=your_exa_key

# Payments
STRIPE_SECRET_KEY=your_stripe_key
STRIPE_WEBHOOK_SECRET=your_webhook_key
STRIPE_PRO_PRICE_ID=price_xxxxx

# App Config
APP_ENV=development
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

### 3. Run the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit [http://localhost:8000/docs](http://localhost:8000/docs) for interactive API documentation.

## 📁 Project Structure

```
backend-career-twin/
├── app/
│   ├── main.py                 # FastAPI app & router setup
│   ├── config.py               # Settings & environment config
│   ├── dependencies.py         # Dependency injection
│   ├── analysis/               # Resume analysis module
│   ├── resume/                 # Resume management
│   ├── job_search/             # Job search & matching
│   ├── cold_email/             # Email generation
│   ├── advisor/                # Career advice AI
│   ├── recruiter_mail/         # Recruiter outreach
│   ├── auth/                   # Authentication
│   ├── billing/                # Stripe billing
│   ├── agents/                 # AI agent implementations
│   ├── graph/                  # LangGraph workflows
│   ├── db/                     # Database client & queries
│   └── core/                   # Shared models & exceptions
├── tests/                      # Test suite
├── Dockerfile                  # Container definition
├── cloudbuild.yaml            # GCP Cloud Build config
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

## 🔌 API Endpoints

### Analysis
- `POST /analysis/resume` - Analyze resume from recruiter perspective
- `POST /analysis/compare` - Compare resume vs job description

### Resume
- `GET /resume` - Get user's resume
- `POST /resume` - Upload/create resume
- `PUT /resume` - Update resume

### Job Search
- `GET /job_search` - Search jobs
- `POST /job_search/match` - Find matching jobs

### Cold Email
- `POST /cold_email/generate` - Generate personalized cold email

### Advisor
- `POST /advisor/suggest` - Get career advice
- `POST /advisor/strategy` - Get career strategy

### Auth
- `POST /auth/signup` - Register new user
- `POST /auth/login` - Login user
- `POST /auth/verify` - Verify token

### Billing
- `GET /billing/usage` - Get API usage
- `POST /billing/upgrade` - Upgrade to Pro

See [http://localhost:8000/docs](http://localhost:8000/docs) for full API reference.

## 🤖 AI Agents

### Baseline Agent
Entry point for routing requests to appropriate specialized agents.

### Resume Agent
Analyzes resumes and generates AI feedback from recruiter perspective.

### JD Agent
Processes job descriptions and matches against resumes.

### Recruiter Agent
Simulates recruiter behavior and evaluation criteria.

### Company Agent
Researches companies and generates insights (uses Tavily).

### Rewrite Agent
Improves and rewrites resume sections.

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_analysis.py

# Run with coverage
pytest --cov=app
```

## 🐳 Docker

### Build Image

```bash
docker build -t career-twin:latest .
```

### Run Container

```bash
docker run -p 8080:8080 \
  -e GROQ_API_KEY=xxx \
  -e SUPABASE_URL=xxx \
  -e SUPABASE_SERVICE_KEY=xxx \
  career-twin:latest
```

## ☁️ GCP Deployment

### Option 1: Cloud Build (Recommended)

GCP Cloud Build automatically deploys on push to `main` branch.

**Setup:**
1. Configure substitution variables in Cloud Build trigger
2. Push to main: `git push origin main`
3. Monitor build in [Cloud Build console](https://console.cloud.google.com/cloud-build/builds)

See [CLOUD_BUILD_SETUP.md](CLOUD_BUILD_SETUP.md) for detailed setup.

### Option 2: GitHub Actions

Uses Workload Identity Federation (no API keys in GitHub).

**Setup:**
1. Set GitHub Secrets (see `.github/workflows/deploy-gcp.yml`)
2. Push to main or deploy branch
3. Monitor in Actions tab

See [GCP_DEPLOYMENT_SETUP.md](GCP_DEPLOYMENT_SETUP.md) for WIF setup.

### Deploy to Cloud Run Manually

```bash
# Authenticate
gcloud auth login
gcloud config set project career-twin-497307

# Build & push
docker build -t europe-west1-docker.pkg.dev/career-twin-497307/cloud-run-source-deploy/backend-career-twin/backend-carniq:latest .
docker push europe-west1-docker.pkg.dev/career-twin-497307/cloud-run-source-deploy/backend-career-twin/backend-carniq:latest

# Deploy
gcloud run deploy backend-carniq \
  --image=europe-west1-docker.pkg.dev/career-twin-497307/cloud-run-source-deploy/backend-career-twin/backend-carniq:latest \
  --region=europe-west1 \
  --allow-unauthenticated \
  --set-env-vars=GROQ_API_KEY=xxx,SUPABASE_URL=xxx,... \
  --memory=2Gi --cpu=2
```

## 📊 Database

### Setup Supabase

1. Create Supabase project at [supabase.com](https://supabase.com)
2. Run migrations: `psql -f supabase_schema.sql`
3. Get credentials from project settings

### Schema

See [supabase_schema.sql](supabase_schema.sql) for database structure.

## 🔐 Authentication

Uses JWT tokens via Supabase Auth. Include in request headers:

```bash
Authorization: Bearer <your_jwt_token>
```

## 💳 Billing

**Free Tier:**
- 3 API calls/month
- Limited to basic analysis

**Pro Tier ($9.99/month):**
- Unlimited API calls
- Priority processing
- Email generation
- Company research

Managed via Stripe webhooks at `/billing/webhook`.

## 📝 Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ | Groq API key (if using Groq) |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key (if using Gemini) |
| `LLM_PROVIDER` | ✅ | "groq" or "gemini" |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | Supabase service role key |
| `SUPABASE_JWT_SECRET` | ✅ | JWT signing secret |
| `TAVILY_API_KEY` | ✅ | Tavily web search API |
| `HUNTER_API_KEY` | ✅ | Hunter.io email finder API |
| `EXA_API_KEY` | ✅ | Exa semantic search API |
| `STRIPE_SECRET_KEY` | ✅ | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | ✅ | Stripe webhook secret |
| `STRIPE_PRO_PRICE_ID` | ✅ | Pro tier price ID |
| `APP_ENV` | ⚪ | "development" or "production" |
| `CORS_ORIGINS` | ⚪ | Comma-separated allowed origins |

## 🚨 Error Handling

API returns standardized error responses:

```json
{
  "detail": "Error message",
  "error_code": "ERROR_CODE",
  "status": 400
}
```

Check [app/core/exceptions.py](app/core/exceptions.py) for error types.

## 📈 Performance

- **Async Processing** - All I/O operations are async
- **Caching** - LangGraph compilations cached at startup
- **Streaming** - Resume analysis supports streaming responses
- **Rate Limiting** - Free tier: 3 requests/month (configurable)

## 🛠️ Development

### Code Style

```bash
# Format code
black app tests

# Lint
flake8 app tests

# Type checking
mypy app
```

### Adding New Features

1. Create module in `app/`
2. Add router to `main.py`
3. Add tests in `tests/`
4. Update API docs in docstrings
5. Create migration for DB changes

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feat/your-feature`
3. Commit changes: `git commit -am 'Add feature'`
4. Push to branch: `git push origin feat/your-feature`
5. Open Pull Request

## 📞 Support

For issues and questions:
- Open a GitHub Issue
- Check existing documentation
- Review API docs at `/docs` endpoint

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details.

## 🔗 Resources

- [FastAPI Docs](https://fastapi.tiangolo.com)
- [Supabase Docs](https://supabase.com/docs)
- [LangChain Docs](https://python.langchain.com)
- [GCP Cloud Run Docs](https://cloud.google.com/run/docs)
- [Stripe API Docs](https://stripe.com/docs/api)

---

**Built with ❤️ by the Career Twin team**
