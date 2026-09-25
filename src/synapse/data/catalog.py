from dataclasses import dataclass


@dataclass(frozen=True)
class TenantSpec:
    slug: str
    code: str
    name: str
    flagship_key: str
    flagship_name: str
    extra_keys: tuple[str, ...]
    extra_names: tuple[str, ...]


TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        slug="northwind",
        code="nw",
        name="Northwind Logistics",
        flagship_key="ATLAS",
        flagship_name="Atlas Platform Modernisation",
        extra_keys=("HARBOR", "QUAY", "BEACON"),
        extra_names=("Harbor Identity", "Quay Billing", "Beacon Observability"),
    ),
    TenantSpec(
        slug="globex",
        code="gx",
        name="Globex Health",
        flagship_key="LUMEN",
        flagship_name="Lumen Patient Portal",
        extra_keys=("RIDGE", "PULSE"),
        extra_names=("Ridge Data Platform", "Pulse Notifications"),
    ),
    TenantSpec(
        slug="initech",
        code="in",
        name="Initech Finance",
        flagship_key="COBALT",
        flagship_name="Cobalt Ledger",
        extra_keys=("LEDGER", "VAULT"),
        extra_names=("Ledger Close Automation", "Vault Controls"),
    ),
)

FIRST_NAMES = (
    "Amina",
    "Ben",
    "Priya",
    "Diego",
    "Elena",
    "Farid",
    "Greta",
    "Hiro",
    "Imani",
    "Jonas",
    "Keiko",
    "Luis",
    "Mara",
    "Nabil",
    "Olga",
    "Pavel",
    "Quinn",
    "Rosa",
    "Sanjay",
    "Talia",
    "Uma",
    "Viktor",
    "Wanjiru",
    "Xiao",
    "Yara",
    "Zane",
    "Chris",
    "Deepa",
)

LAST_NAMES = (
    "Okoye",
    "Shah",
    "Nguyen",
    "Berg",
    "Costa",
    "Diallo",
    "Ellis",
    "Fujimoto",
    "Garcia",
    "Hassan",
    "Iversen",
    "Joshi",
    "Khan",
    "Lopez",
    "Mwangi",
    "Novak",
    "Okafor",
    "Patel",
    "Quinn",
    "Reeves",
    "Saito",
    "Tran",
    "Ueda",
    "Vega",
    "Wright",
    "Young",
    "Zimmerman",
    "Adler",
)

TEAM_NAMES = (
    "Platform",
    "Delivery",
    "Reliability",
    "Data",
    "Security",
    "Product",
    "Integrations",
    "Mobile",
)

WORK_NOUNS = (
    "ingest pipeline",
    "SDK client",
    "feature-flag service",
    "audit log",
    "search index",
    "export job",
    "SSO adapter",
    "rate limiter",
    "webhook dispatcher",
    "schema migration",
    "onboarding wizard",
    "reconciliation job",
    "access review",
    "retry queue",
    "cache invalidation",
    "document parser",
    "notification template",
    "billing meter",
    "tenant provisioner",
    "dead-letter replay",
)
