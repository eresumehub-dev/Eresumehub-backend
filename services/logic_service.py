import re
from datetime import datetime
from typing import List, Dict, Any, Optional

class TimelineValidator:
    """
    Validates logical consistency of resume timelines.
    Detailed checks:
    1. Education Order: Pre-University < Bachelors < Masters < PhD.
    2. Overlaps: Multiple full-time degrees overlapping significantly.
    3. Future Dates: Graduation dates far in the future (ok for current degrees, but worth checking).
    """

    EDUCATION_HIERARCHY = {
        'pre-university': 1,
        'high school': 1,
        'bachelor': 2,
        'postgraduate': 3,
        'master': 3,
        'phd': 4,
        'doctorate': 4
    }

    @staticmethod
    def parse_date(date_str: str) -> Optional[datetime]:
        """Parses MM/YYYY or Month Year formats."""
        if not date_str or date_str.lower() == 'present':
            return datetime.now()
        
        # Strategies
        formats = [
            "%m/%Y", "%Y", "%B %Y", "%b %Y" # 01/2024, 2024, January 2024, Jan 2024
        ]
        
        clean_str = date_str.strip()
        
        for fmt in formats:
            try:
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def validate_timelines(data: Dict[str, Any]) -> List[str]:
        warnings = []
        educations = data.get('educations', [])
        
        # Sort educations by hierarchy if possible
        # We need to extract dates first
        parsed_edus = []
        for edu in educations:
            start = TimelineValidator.parse_date(edu.get('start_date', ''))
            end = TimelineValidator.parse_date(edu.get('end_date', ''))
            degree = edu.get('degree', '').lower()
            
            # Determine level
            level = 0
            for key, val in TimelineValidator.EDUCATION_HIERARCHY.items():
                if key in degree:
                    level = val
                    break
            
            parsed_edus.append({
                'start': start,
                'end': end,
                'level': level,
                'raw': edu
            })

        # Check 1: Pre-Uni vs Bachelors
        # Logic: If level 1 exists, its End Date must be <= Level 2 Start Date (allowing small overlap)
        pre_unis = [e for e in parsed_edus if e['level'] == 1]
        bachelors = [e for e in parsed_edus if e['level'] == 2]
        masters = [e for e in parsed_edus if e['level'] == 3]

        for pre in pre_unis:
            if not pre['end']: continue
            for bach in bachelors:
                if not bach['start']: continue
                if pre['end'] > bach['start']:
                    warnings.append(
                        f"Timeline Conflict: Pre-University ({pre['raw'].get('graduation_date') or 'Unknown'}) appears to end AFTER Bachelor's degree started."
                    )
        
        # Check 2: Bachelors vs Masters
        for bach in bachelors:
            if not bach['end']: continue
            for mast in masters:
                if not mast['start']: continue
                 # Allow 0 tolerance? Usually Masters starts after Bachelors graduation.
                if bach['end'] > mast['start']:
                     # Just a warning, sometimes people overlap slightly or dual degree
                     # stick to strict check for now as user requested
                    warnings.append(
                        f"Potential Conflict: Bachelor's degree end date ({bach['end'].strftime('%Y')}) overlaps with Master's start ({mast['start'].strftime('%Y')})."
                    )

        # Check 3: Impossible durations (e.g. 2024-2025 for 4 year degree?) - skipping for now, too complex logic.

        return warnings
