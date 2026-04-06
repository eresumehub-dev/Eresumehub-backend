import logging
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

class HTMLGenerator:
    """
    Utility class for generating HTML from Jinja2 templates for resumes.
    """
    _env = Environment(loader=FileSystemLoader("templates"))

    @staticmethod
    def generate_html(text: str, full_name: str, contact_info: dict, user_data: dict, rag_data: dict, template_style: str = "professional") -> str:
        """
        Generate HTML resume using Jinja2 templates and RAG data.
        """
        template_name = f"resume_{template_style}.jinja2"
        
        try:
            template = HTMLGenerator._env.get_template(template_name)
        except Exception as e:
            logger.warning(f"Template '{template_name}' not found, falling back to professional. Error: {e}")
            template = HTMLGenerator._env.get_template("resume_professional.jinja2")

        import re
        def parse_rag_order(rag_order_list):
            if not rag_order_list:
                return ["summary", "experience", "projects", "education", "certifications", "skills", "languages", "motivation"]
            
            # Map known keywords in string to section keys
            ordered_keys = []
            for item in rag_order_list:
                item_lower = item.lower()
                if "summary" in item_lower: ordered_keys.append("summary")
                elif "experience" in item_lower: ordered_keys.append("experience")
                elif "education" in item_lower: ordered_keys.append("education")
                elif "skill" in item_lower: ordered_keys.append("skills")
                elif "language" in item_lower: ordered_keys.append("languages")
                elif "certif" in item_lower: ordered_keys.append("certifications")
                elif "project" in item_lower: ordered_keys.append("projects")
            
            # Ensure projects and motivation are added if they exist but aren't strictly in the list
            for key in ["projects", "motivation"]:
                if key not in ordered_keys:
                    ordered_keys.append(key)
            return ordered_keys

        def format_date_for_region(date_str, format_rule="DD.MM.YYYY"):
            if not date_str or not isinstance(date_str, str): return date_str
            # Simple conversion of YYYY-MM-DD or YYYY-MM to DD.MM.YYYY / MM.YYYY
            match = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", date_str)
            if match:
                y, m, d = match.groups()
                if format_rule.startswith("DD.MM.YYYY"):
                    return f"{d}.{m}.{y}" if d else f"{m}.{y}"
            return date_str

        # SANITIZATION: Clean text fields to prevent xhtml2pdf "word gaps" bug and minor AI formatting issues (like missing spaces)
        def clean_field(t):
            if not t or not isinstance(t, str): return t
            # Fix spacing after punctuation "word.Word" -> "word. Word"
            t = re.sub(r'([a-z])([.,;!])([A-Za-z])', r'\1\2 \3', t)
            return " ".join(t.split())
            
        def normalize_bullets(items):
            if not items: return []
            if isinstance(items, str):
                # When frontend sends a continuous string block, it often lacks spaces before capital letters.
                # "applicationsBuilt" -> "applications\nBuilt"
                items = re.sub(r'([a-z])([A-Z])', r'\1\n\2', items)
                # Split by newline or period (if it's a paragraph instead of bullet points)
                parts = re.split(r'\n|(?<=[a-z])\.\s+', items)
                return [clean_field(b) for b in parts if b and b.strip()]
            if isinstance(items, list):
                return [clean_field(str(b)) for b in items if b and str(b).strip()]
            return [clean_field(str(items))]

        # Create a safe copy of user_data to avoid side-effects on shared objects
        safe_user_data = user_data.copy()
        safe_user_data["full_name"] = clean_field(safe_user_data.get("full_name", full_name))
        
        # Consistent Capitalization for Role & Title
        import re
        def normalize_title(text):
            if not text: return text
            # Normalize casing for technical terms (e.g. Ai -> AI, Api -> API)
            text = str(text).title()
            text = re.sub(r'\bAi\b', 'AI', text)
            text = re.sub(r'\bApi\b', 'API', text)
            return text

        if safe_user_data.get("headline"):
            safe_user_data["headline"] = normalize_title(clean_field(safe_user_data["headline"]))
        if safe_user_data.get("title"):
            safe_user_data["title"] = normalize_title(safe_user_data.get("title"))

        # Fix Empty Bullets Bug: Enforce lists for achievements/description
        if safe_user_data.get("experience"):
            for exp in safe_user_data["experience"]:
                if exp.get("achievements"):
                    exp["achievements"] = normalize_bullets(exp["achievements"])
                if exp.get("description"):
                    exp["description"] = normalize_bullets(exp["description"])
                if exp.get("job_title"):
                    exp["job_title"] = normalize_title(exp["job_title"])
                if exp.get("title"):
                    exp["title"] = normalize_title(exp["title"])
                    
        if safe_user_data.get("projects"):
            for proj in safe_user_data["projects"]:
                if proj.get("description"):
                    proj["description"] = normalize_bullets(proj["description"])
                if proj.get("achievements"):
                    proj["achievements"] = normalize_bullets(proj["achievements"])

        # Extract dynamic values
        cv_order = rag_data.get("knowledge_base", {}).get("cv_structure", {}).get("order", [])
        date_format = rag_data.get("knowledge_base", {}).get("cultural_rules", {}).get("date_format", "DD.MM.YYYY")
        
        # Render the template
        try:
            return template.render(
                text=text,
                full_name=full_name,
                contact_info=contact_info,
                user_data=safe_user_data,
                rag_data=rag_data,
                template_style=template_style,
                section_order=parse_rag_order(cv_order),
                format_date=lambda d: format_date_for_region(d, date_format)
            )
        except Exception as e:
            logger.error(f"Error rendering HTML template: {e}")
            raise e
