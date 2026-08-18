import asyncio
from pathlib import Path
from unittest.mock import patch
import pytest

import archguard.contract.llm_inference as li
from archguard.llm.cloud import CloudLLMExplainer, PRIMARY_MODEL, FALLBACK_MODEL

@pytest.mark.asyncio
async def test_generate_contract_from_llm_fallback():
    """Verify that if PRIMARY_MODEL fails, it falls back to FALLBACK_MODEL via _call_api."""
    call_log = []
    
    def side_effect(self, prompt, model, system=''):
        call_log.append(model)
        if model == PRIMARY_MODEL:
            raise RuntimeError('rate limited')
        return '{"modules": []}', 'stop'

    with patch.object(CloudLLMExplainer, '_call_api', autospec=True, side_effect=side_effect):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'gemini-fake-key'}):
            # Need to mock the filesystem operations to avoid errors
            with patch('archguard.contract.llm_inference._build_directory_tree', return_value=""), \
                 patch('archguard.contract.llm_inference._extract_module_docstrings', return_value=""), \
                 patch('archguard.contract.llm_inference._read_readme_excerpt', return_value=""), \
                 patch('archguard.contract.validator.validate_contract'):
                
                result = await li.generate_contract_from_llm(Path('.'))
                
                assert call_log == [PRIMARY_MODEL, FALLBACK_MODEL]
                assert result == {"modules": []}
