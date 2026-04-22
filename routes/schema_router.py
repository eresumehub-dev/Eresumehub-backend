import os
import json
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any

router = APIRouter(prefix="/api/v1/schemas", tags=["Schemas"])

RAG_SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag_schemas")

@router.get("/countries", response_model=List[str])
async def get_available_countries():
    """
    Get a list of countries available in the RAG schemas.
    """
    try:
        if not os.path.exists(RAG_SCHEMAS_DIR):
            return []
        
        countries = []
        for item in os.listdir(RAG_SCHEMAS_DIR):
            item_path = os.path.join(RAG_SCHEMAS_DIR, item)
            if os.path.isdir(item_path):
                # Check if knowledge_base.json exists in the directory
                if os.path.exists(os.path.join(item_path, "knowledge_base.json")):
                    countries.append(item)
        
        return sorted(countries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list countries: {str(e)}")

@router.get("/{country}", response_model=Dict[str, Any])
async def get_country_schema(country: str):
    """
    Get the knowledge base schema for a specific country.
    """
    try:
        # 🧬 v16.4.19 - Case-Insensitive Normalization
        c_norm = country.strip().lower()
        if c_norm == 'dach':
            c_norm = 'germany'
            
        # Find the actual directory that matches the normalized name
        actual_dir = None
        if os.path.exists(RAG_SCHEMAS_DIR):
            for d in os.listdir(RAG_SCHEMAS_DIR):
                if d.lower() == c_norm and os.path.isdir(os.path.join(RAG_SCHEMAS_DIR, d)):
                    actual_dir = d
                    break

        if not actual_dir:
            raise HTTPException(status_code=404, detail=f"Schema not found for country: {country}")
            
        schema_path = os.path.join(RAG_SCHEMAS_DIR, actual_dir, "knowledge_base.json")
        
        if not os.path.exists(schema_path):
            raise HTTPException(status_code=404, detail=f"knowledge_base.json missing for {actual_dir}")
            
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
            
        return schema
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch schema: {str(e)}")
