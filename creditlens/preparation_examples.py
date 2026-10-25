"""Synthetic OCR fixtures bound to public registration metadata, never model results."""
from copy import deepcopy
from pathlib import Path

from .asset_preflight import preflight_transfer_assets


def preparation_examples(root: Path) -> dict:
    inventory = preflight_transfer_assets(root)
    document = inventory['documents'][0]
    pages = []
    for page in document['pages']:
        # Deliberately invented marker text and geometry. No PDF/image is read.
        quad = [[20,20],[80,20],[80,40],[20,40]]
        pages.append({'schema_version':1,'provenance':'synthetic','document_id':document['id'],
          'source_pdf_sha256':document['source_pdf_sha256'],'image_sha256':page['image_sha256'],
          'ocr_input':{'schema_version':1,**{k:page[k] for k in ('page','width','height','image_width','image_height')},
            'result':{'boxes':[quad],'txts':['SAMPLE'],'scores':[1.0],
                      'word_results':[[['SAMPLE',1.0,deepcopy(quad)]]]}}})
    complete = {'schema_version':1,'document_id':document['id'],'provenance':'synthetic','pages':pages}
    missing = deepcopy(complete);missing['pages']=missing['pages'][:-1]
    damaged = deepcopy(complete);damaged['pages'][-1]['ocr_input']['result']['boxes'][0][2][0]=-1
    return {'schema_version':1,'provenance':'synthetic','model_inference_performed':False,
      'source_bytes_read':False,'document_id':document['id'],'expected_pages':[p['page'] for p in document['pages']],
      'scope':'Public registration metadata with invented SAMPLE text and boxes; no financial extraction result.',
      'examples':{'missing':missing,'damaged':damaged,'complete':complete}}
