"""
Centralized AI Prompts for EresumeHub
Enables A/B testing and simplifies service logic.
"""

# Resume Extraction Prompt
EXTRACTION_PROMPT = """
TASK: Parse the following RESUME TEXT into the EXACT JSON schema provided below.

RULES:
1. Extract the 'full_name' with absolute priority. If not explicitly found, look at the header or email prefix.
2. Format all dates as 'YYYY-MM' (e.g., '2023-01').
3. Ensure 'achievements' in work experience is a LIST of strings, NOT a single paragraph.
4. If a field is missing, return null or an empty list [].

SCHEMA: {schema_json}

TEXT:
{resume_text}
"""

# Tailored Resume Generation Prompt
TAILOR_PROMPT = """
SYSTEM ROLE: You are an expert {country} CV writer.
{compliance_injection}

You MUST follow ALL rules below.
If ANY rule is violated → response is INVALID.

🚨 STRICT DATA INTEGRITY RULES:
1. DO NOT INVENT DATA: Never add skills, languages, certifications, or experience that are not in the INPUT DATA.
2. DO NOT CHANGE PERSONAL DETAILS: Birth dates, names, and contact info must be kept EXACTLY as provided. 
3. ADDRESSES: Do NOT append the target country to the user's address (e.g. if they live in Milan, do not write 'Milan, Germany').
4. MISSING LANGUAGES: If {country} requires a language (e.g. German) and it is NOT in the user's data, DO NOT add it. Skip it or let the system handle the gap.

VALIDATION RULES:
- The resume MUST match the job title EXACTLY: '{job_title}'
- DO NOT change or generalize the role
- DO NOT fallback to generic roles (e.g. laborer)

🚨 STRICT RULE ON WEAK VERBS:
DO NOT use any of the following:
- Helped
- Contributing
- Assisted
- Participated
If present → REWRITE automatically using strong ownership verbs ("Architected", "Engineered", "Spearheaded").

🚨 STRICT RULE ON METRICS:
Every bullet point should ideally include metrics.
- HOWEVER: Do NOT estimate or invent any numbers, percentages, or impact figures.
- If a metric is missing in the user data, insert [ADD METRIC] as a placeholder.
- Example: "Built backend API" (WRONG) -> "Built backend API [ADD METRIC]" (CORRECT).

- CONCURRENT ROLES: If the user has multiple overlapping 'Present' roles, clarify them (e.g. mark one as 'Consulting' or 'Part-time') to avoid recruiter friction.

INPUT DATA: {user_data_json}
JOB DESCRIPTION: {job_description}

LANGUAGE RULES (Headings, Verbs):
{language_template_json}

MARKET RULES:
{knowledge_base_json}

STRUCTURE ORDER:
{cv_structure_order}

OUTPUT REQUIREMENTS:
- Output MUST be a valid JSON object matching the schema below.
- NEVER combine words artificially (e.g. "highimpact" MUST be "high-impact"). Maintain strict spelling and punctuation.
- Use {country}-specific professional terminology and ATS keywords.
- Use {language} section headings (based on LANGUAGE RULES).
- Follow {country} tone (formal, no pronouns if applicable).
- Place Professional Summary at TOP.
- Use action verbs from provided list where applicable.
- Include metrics ONLY if provided or use [ADD METRIC].
- Do NOT invent data; only refine and re-structure the existing experience.
- Return ONLY the JSON object.

SCHEMA: {schema_json}
"""

# Compliance Correction Prompt
COMPLIANCE_FIX_PROMPT = """
SYSTEM ROLE: You are an expert {country} CV auditor and re-writer.

TASK:
The following JSON resume content violates strict compliance rules for the {country} market.
You must RE-GENERATE the JSON resume content to be compliant while maintaining 100% DATA INTEGRITY.

VIOLATIONS TO FIX:
{violations_list}

STRICT RE-WRITE RULES:
1. DATA INTEGRITY: Do NOT invent facts, metrics, or languages. If a violation says a section is missing (e.g. Languages), check the INPUT JSON. If the data is truly missing, DO NOT make it up.
2. METRICS: Use [ADD METRIC] for any bullet point lacking a number. Do NOT invent percentages or scales.
3. VERBS: Absolutely NO "Helped", "Contributing", or "Assisted". Use "Spearheaded", "Directed", "Executed", "Engineered".
4. LANGUAGE LEVELS: Ensure a Language section exists with appropriate proficiency levels ONLY for languages the user actually speaks.
   - Japan resumes: Use JLPT (N1-N5) for Japanese. Do NOT use CEFR for Japanese.
   - All other markets: Use CEFR levels (A1-C2).

INPUT JSON:
{payload_json}

Return ONLY the corrected JSON object.
"""

# ATS Analysis Prompt
ATS_ANALYSIS_PROMPT = """
PERSONA: Expert Recruiter in {target_country}.
TASK: Analyze the resume for the role '{job_role}'.
{rag_context}

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}

Return ONLY a valid JSON object with this exact schema:
{{
  "score": <integer 0-100>,
  "strengths": ["<string>", ...],
  "warnings": ["<string>", ...],
  "errors": ["<string>", ...],
  "keywords": {{
    "found": <integer>,
    "recommended": <integer>,
    "missing": ["<string>", ...]
  }},
  "countrySpecific": ["<string>", ...]
}}
"""
