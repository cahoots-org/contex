"""Seed sample data for Contex demo"""

import requests
import json

BASE_URL = "http://localhost:8001"

# Sample data for a realistic SaaS application
SAMPLE_DATA = [
    {
        "project_id": "saas-app",
        "data_key": "authentication_system",
        "data": {
            "type": "architecture",
            "component": "Auth Service",
            "description": "OAuth2 + JWT authentication with refresh tokens",
            "technologies": ["OAuth2", "JWT", "Redis", "PostgreSQL"],
            "endpoints": [
                "/api/v1/auth/login",
                "/api/v1/auth/logout",
                "/api/v1/auth/refresh",
                "/api/v1/auth/verify"
            ],
            "security_features": [
                "Rate limiting (100 req/min)",
                "MFA support (TOTP)",
                "Session management",
                "IP allowlisting"
            ],
            "file": "backend/services/auth_service.py"
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "user_database_schema",
        "data": {
            "type": "database",
            "database": "PostgreSQL 15",
            "schema": "public",
            "tables": {
                "users": {
                    "columns": ["id", "email", "password_hash", "created_at", "last_login"],
                    "indexes": ["email_unique_idx", "created_at_idx"]
                },
                "user_sessions": {
                    "columns": ["id", "user_id", "token_hash", "expires_at", "ip_address"],
                    "indexes": ["user_id_idx", "expires_at_idx"]
                },
                "user_profiles": {
                    "columns": ["user_id", "display_name", "avatar_url", "bio", "preferences"],
                    "foreign_keys": ["user_id -> users.id"]
                }
            },
            "description": "User management database schema with authentication and profile data"
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "payment_integration",
        "data": {
            "type": "integration",
            "service": "Stripe",
            "description": "Payment processing with Stripe API for subscriptions and one-time payments",
            "capabilities": [
                "Subscription billing",
                "One-time payments",
                "Invoice generation",
                "Webhook handling",
                "Payment method management"
            ],
            "webhooks": [
                "payment_intent.succeeded",
                "customer.subscription.updated",
                "invoice.payment_failed"
            ],
            "file": "backend/integrations/stripe_client.py"
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "api_rate_limiting",
        "data": {
            "type": "middleware",
            "component": "Rate Limiter",
            "description": "Redis-backed rate limiting middleware with tiered limits",
            "strategy": "Token bucket algorithm",
            "tiers": {
                "free": "100 requests/hour",
                "pro": "1000 requests/hour",
                "enterprise": "10000 requests/hour"
            },
            "implementation": "backend/middleware/rate_limiter.py"
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "frontend_tech_stack",
        "data": {
            "type": "technology",
            "layer": "Frontend",
            "description": "Modern React-based SPA with TypeScript",
            "framework": "React 18",
            "language": "TypeScript 5.0",
            "build_tool": "Vite",
            "state_management": "Redux Toolkit",
            "ui_library": "Material-UI v5",
            "testing": ["Jest", "React Testing Library", "Cypress"],
            "key_libraries": [
                "react-router-dom",
                "axios",
                "react-query",
                "formik",
                "yup"
            ]
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "error_handling_strategy",
        "data": {
            "type": "architecture",
            "component": "Error Handling",
            "description": "Centralized error handling with structured logging and monitoring",
            "error_types": {
                "ValidationError": "400 - Invalid user input",
                "AuthenticationError": "401 - Invalid credentials",
                "AuthorizationError": "403 - Insufficient permissions",
                "NotFoundError": "404 - Resource not found",
                "RateLimitError": "429 - Too many requests",
                "ServerError": "500 - Internal server error"
            },
            "monitoring": {
                "service": "Sentry",
                "features": ["Error tracking", "Performance monitoring", "Release tracking"]
            },
            "logging": {
                "format": "JSON",
                "levels": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                "destinations": ["CloudWatch", "Datadog"]
            }
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "email_service",
        "data": {
            "type": "service",
            "component": "Email Service",
            "description": "Transactional email service using SendGrid",
            "provider": "SendGrid",
            "templates": [
                "welcome_email",
                "password_reset",
                "email_verification",
                "subscription_renewal",
                "payment_receipt"
            ],
            "features": [
                "Template rendering",
                "Batch sending",
                "Delivery tracking",
                "Bounce handling",
                "Unsubscribe management"
            ]
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "caching_strategy",
        "data": {
            "type": "architecture",
            "component": "Caching Layer",
            "description": "Multi-tier caching strategy with Redis and CDN",
            "layers": {
                "CDN": {
                    "provider": "CloudFlare",
                    "cached": ["Static assets", "Public pages"]
                },
                "Application": {
                    "provider": "Redis",
                    "cached": ["User sessions", "API responses", "Database queries"],
                    "ttl": "5-60 minutes depending on data type"
                },
                "Database": {
                    "provider": "PostgreSQL query cache",
                    "strategy": "Materialized views for analytics"
                }
            }
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "deployment_pipeline",
        "data": {
            "type": "devops",
            "component": "CI/CD Pipeline",
            "description": "Automated deployment pipeline with GitHub Actions",
            "stages": [
                "Lint and format check",
                "Unit tests",
                "Integration tests",
                "Security scanning",
                "Build Docker images",
                "Deploy to staging",
                "E2E tests",
                "Deploy to production"
            ],
            "infrastructure": {
                "platform": "AWS ECS",
                "container_registry": "ECR",
                "orchestration": "ECS Fargate",
                "load_balancer": "ALB"
            },
            "monitoring": ["CloudWatch", "Datadog APM", "Sentry"]
        }
    },
    {
        "project_id": "saas-app",
        "data_key": "analytics_tracking",
        "data": {
            "type": "integration",
            "component": "Analytics",
            "description": "User behavior tracking and product analytics",
            "providers": {
                "product_analytics": "Mixpanel",
                "web_analytics": "Google Analytics 4",
                "session_replay": "FullStory"
            },
            "tracked_events": [
                "user_signup",
                "user_login",
                "feature_used",
                "subscription_started",
                "subscription_cancelled",
                "payment_completed"
            ],
            "privacy": {
                "gdpr_compliant": True,
                "data_retention": "2 years",
                "anonymization": "IP addresses hashed"
            }
        }
    }
]

def seed_data():
    """Seed sample data into Contex"""
    print("🌱 Seeding sample data...")
    print("=" * 60)

    success_count = 0
    for item in SAMPLE_DATA:
        try:
            response = requests.post(
                f"{BASE_URL}/api/data/publish",
                json=item,
                timeout=5
            )
            response.raise_for_status()
            result = response.json()
            print(f"✓ Published: {item['data_key']}")
            success_count += 1
        except Exception as e:
            print(f"✗ Failed to publish {item['data_key']}: {e}")

    print("=" * 60)
    print(f"✅ Successfully seeded {success_count}/{len(SAMPLE_DATA)} data items")
    print()
    print("Now visit http://localhost:8001/ and try these queries:")
    print("  • 'Show me authentication and security features'")
    print("  • 'What payment integrations do we have?'")
    print("  • 'Find database schemas and tables'")
    print("  • 'List all error handling strategies'")
    print("  • 'What technologies are we using?'")

if __name__ == "__main__":
    seed_data()
