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

        # SANITIZATION: Clean text fields to prevent xhtml2pdf "word gaps" bug
        def clean_field(t):
            if not t or not isinstance(t, str): return t
            return " ".join(t.split())

        # Create a safe copy of user_data to avoid side-effects on shared objects
        safe_user_data = user_data.copy()
        safe_user_data["full_name"] = clean_field(safe_user_data.get("full_name", full_name))
        if safe_user_data.get("headline"):
            safe_user_data["headline"] = clean_field(safe_user_data["headline"])

        # Render the template
        try:
            return template.render(
                text=text,
                full_name=full_name,
                contact_info=contact_info,
                user_data=safe_user_data,
                rag_data=rag_data,
                template_style=template_style
            )
        except Exception as e:
            logger.error(f"Error rendering HTML template: {e}")
            raise e
