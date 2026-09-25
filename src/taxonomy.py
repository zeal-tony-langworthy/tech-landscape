"""
The skills taxonomy: the single source of truth for categories and subcategories.

The review prompt is generated from this, and review/apply validate against it,
so edit it here and both stay in sync. Order here is the order on the page.
"""

TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Programming Languages": {
        "General Purpose": ["Java", "Python", "C#", "Go", "Kotlin"],
        "Web & Scripting": ["JavaScript", "TypeScript", "PHP", "Ruby"],
        "Systems": ["C", "C++", "Rust"],
    },
    "Frameworks & Libraries": {
        "Backend Frameworks": ["Spring Boot", ".NET", "Django", "Express"],
        "Frontend Frameworks": ["React", "Angular", "Vue.js", "Svelte"],
        "Mobile Frameworks": ["Flutter", "React Native"],
    },
    "Databases & Data Stores": {
        "Relational": ["PostgreSQL", "MySQL", "Oracle"],
        "Document": ["MongoDB", "Couchbase"],
        "Key-Value & In-Memory": ["Redis", "Memcached"],
        "Wide-Column": ["Cassandra", "HBase"],
        "Search": ["Elasticsearch", "OpenSearch"],
        "Schema Migration": ["Flyway", "Liquibase"],
    },
    "Data Engineering": {
        "Workflow Orchestration": ["Airflow", "Dagster", "Prefect"],
        "Streaming & Messaging": ["Kafka", "RabbitMQ", "ActiveMQ"],
        "Data Processing": ["Spark", "dbt"],
    },
    "DevOps & CI/CD": {
        "CI/CD Pipelines": ["Jenkins", "Azure Pipelines", "Concourse", "GitHub Actions"],
        "Infrastructure as Code": ["Terraform", "CloudFormation", "Pulumi"],
        "Configuration Management": ["Puppet", "Ansible", "Chef"],
        "Containers & Orchestration": ["Docker", "Kubernetes"],
    },
    "Observability": {
        "APM": ["Dynatrace", "New Relic", "AppDynamics"],
        "Metrics & Monitoring": ["Prometheus", "Grafana"],
        "Log Management": ["Splunk", "ELK"],
    },
    "Security": {
        "Application Security & SCA": ["Snyk", "SonarQube", "Veracode"],
        "Identity & Access": ["Okta", "Keycloak"],
    },
    "Cloud Platforms": {
        "Public Cloud": ["AWS", "Azure", "GCP"],
    },
}


def render_taxonomy() -> str:
    """The taxonomy as indented text for the prompt."""
    lines = []
    for category, subcategories in TAXONOMY.items():
        lines.append(category)
        for subcategory, examples in subcategories.items():
            lines.append(f"  - {subcategory} (e.g., {', '.join(examples)})")
    return "\n".join(lines)


def is_valid(category: str | None, subcategory: str | None) -> bool:
    return category in TAXONOMY and subcategory in TAXONOMY[category]