"""Bounded current item -> occupied board READ/drag contract; no control APIs."""
from __future__ import annotations
import copy, hashlib, re
from pathlib import Path
import cv2
import numpy as np
from currency_wars_deployment import _occupied_resources, _compatible_slot
from currency_wars_unit_inspection import _image, _observation, _fresh_request
from currency_wars_perception import find_text

BINDING = ('request_id','snapshot_id','match_id','resume_epoch')

def _squares(rgb, bounds, *, maximum, minimum=24, largest=81):
    x,y,right,bottom=bounds
    crop=rgb[y:bottom,x:right]
    edges=cv2.Canny(cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY),70,180)
    contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for contour in contours:
        a,b,w,h=map(int,cv2.boundingRect(contour))
        if not(minimum<=w<=largest and minimum<=h<=largest and abs(w-h)<=4):continue
        # A full rectangle, not arbitrary positive texture or supplied geometry.
        ink=edges[b:b+h,a:a+w]
        if min(float(strip.mean()/255) for strip in (ink[:3],ink[-3:],ink[:,:3],ink[:,-3:]))<.18:continue
        box=[x+a,y+b,x+a+w,y+b+h]
        if any(abs(box[0]-old[0])<8 and abs(box[1]-old[1])<8 for old in boxes):continue
        boxes.append(box)
    boxes.sort(key=lambda box:(round((box[1]-y)/75),box[0]))
    if len(boxes)>maximum:raise ValueError('visible_square_layout_ambiguous')
    return boxes

def visible_inventory(rgb):
    """Only complete square rows in current bounded native right-side viewport."""
    boxes=_squares(rgb,[1725,233,1889,704],maximum=12,minimum=64)
    result=[]
    for index,box in enumerate(boxes,1):
        if not (1728<=box[0]<=1820 and box[2]<=1885):raise ValueError('inventory_square_geometry_invalid')
        x,y,r,b=box
        inner=rgb[y+5:b-5,x+5:r-5]
        if float(cv2.cvtColor(inner,cv2.COLOR_RGB2GRAY).std())<8:raise ValueError('visible_inventory_square_empty_or_obstructed')
        result.append({'slot':index,'bounds':box,'point':[(x+r)//2,(y+b)//2],
            'origin':'current_original_png_square_geometry','name':None})
    return result

def inventory_target(observation, png, slot):
    if type(slot) is not int or not 1<=slot<=12:raise ValueError('visible_inventory_slot_invalid')
    tiles=visible_inventory(_image(png,observation['snapshot_id']))
    if not 1<=slot<=len(tiles):raise ValueError('current_visible_inventory_square_missing')
    return tiles[slot-1]

def _patch(rgb,box,margin=5):
    x,y,r,b=box
    return rgb[y+margin:b-margin,x+margin:r-margin]

def _same_graphic(first,second):
    if min(first.shape[:2])<12 or min(second.shape[:2])<12:return False
    target=cv2.resize(first,(second.shape[1],second.shape[0]),interpolation=cv2.INTER_AREA)
    if min(float(cv2.cvtColor(target,cv2.COLOR_RGB2GRAY).std()),float(cv2.cvtColor(second,cv2.COLOR_RGB2GRAY).std()))<8:return False
    score=float(np.corrcoef(target.reshape(-1),second.reshape(-1))[0,1])
    return bool(np.isfinite(score) and score>=.95)

def _match_graphics(before,after):
    """Bounded exact matching; duplicates preserved as counts, not greedy aliases."""
    if len(before)!=len(after) or len(before)>12:return False
    from functools import lru_cache
    compatible=[[_same_graphic(first,second) for second in after] for first in before]
    @lru_cache(maxsize=49152)
    def match(i,used):
        if i==len(before):return True
        return any(compatible[i][j] and match(i+1,used|(1<<j))
                   for j in range(len(after)) if not used&(1<<j))
    return match(0,0)

def wear_slots(rgb,card):
    x,y,r,b=card['bounds']
    return _squares(rgb,[x,b-12,r,b+42],maximum=3,minimum=24,largest=42)

def validate_equipment_action(action,request):
    expected={'type','reason','expected_page','item','destination','equipment_read_review'}
    if (not isinstance(action,dict) or set(action)!=expected or action['type']!='equip_inventory_item'
        or action['expected_page']!='preparation' or not isinstance(action['reason'],str) or not action['reason'].strip()
        or not isinstance(action['item'],dict) or set(action['item'])!={'slot'}
        or type(action['item']['slot']) is not int or not 1<=action['item']['slot']<=12
        or not isinstance(action['destination'],dict) or set(action['destination'])!={'row','slot'}
        or action['destination']['row'] not in ('front','back') or type(action['destination']['slot']) is not int):
        raise ValueError('equipment_action_shape_invalid')
    review=action['equipment_read_review']
    keys=set(BINDING)|{'source','capture_request_id','frame_id','item_name','item_effect','purpose',
        'item_roi_sha256','target_roi_sha256','item_unobstructed','target_unobstructed',
        'inventory_fully_visible','plain_equipment','name_evidence'}
    if (not isinstance(review,dict) or set(review)!=keys or review['source']!='supervising_agent'
        or any(review[k] is not True for k in ('item_unobstructed','target_unobstructed','inventory_fully_visible','plain_equipment'))
        or any(not isinstance(review[k],str) or not 1<=len(review[k].strip())<=1000 for k in ('item_name','item_effect','purpose'))
        or any(not isinstance(review[k],str) or not re.fullmatch('[0-9a-f]{64}',review[k]) for k in ('item_roi_sha256','target_roi_sha256'))):
        raise ValueError('current_ROOT_item_identity_and_exposure_review_required')
    if request is not None:
        if (any(review[k]!=request.get(k) for k in BINDING)
            or any(review[k]!=request['observation'].get(k) for k in ('capture_request_id','frame_id'))
            or request.get('kind')!='preparation_strategy' or request.get('preparation_checklist',{}).get('phase')!='lineup_equipment'):
            raise ValueError('equipment_review_not_current_lineup_request')
    return review

def equipment_facts(observation,action,png):
    slots,population,resources=_occupied_resources(observation)
    target=slots.get(('board',action['destination']['row'],action['destination']['slot']))
    if not target or target['status']!='occupied':raise ValueError('equipment_target_not_native_occupied_board')
    rgb=_image(png,observation['snapshot_id'])
    item=inventory_target(observation,png,action['item']['slot'])
    if len(wear_slots(rgb,target))>=3:raise ValueError('equipment_target_slots_full')
    return dict(slots=slots,population=population,resources=resources,item=item,target=target)

def check_equipment(action,request,actual,current_png):
    review=validate_equipment_action(action,request);current=_fresh_request(request)
    for observation in (request['observation'],actual):_observation(observation,request['observation']['fields']['stage'],current)
    first=_image(request['original_png'],request['snapshot_id']);second=_image(current_png,actual['snapshot_id'])
    old=equipment_facts(request['observation'],action,request['original_png']);new=equipment_facts(actual,action,current_png)
    if old['population']!=new['population'] or old['resources']!=new['resources']:raise ValueError('equipment_resources_changed')
    if any(not _compatible_slot(slot,new['slots'][key]) for key,slot in old['slots'].items()):raise ValueError('equipment_roster_changed')
    for key,digest in (('item','item_roi_sha256'),('target','target_roi_sha256')):
        box=old[key]['bounds'];x,y,r,b=box
        if box!=new[key]['bounds'] or hashlib.sha256(first[y:b,x:r].tobytes()).hexdigest()!=review[digest]:
            raise ValueError('equipment_original_ROI_digest_or_geometry_changed')
        if not np.array_equal(first[y:b,x:r],second[y:b,x:r]):raise ValueError('equipment_current_source_or_target_ROI_changed')
    if not _match_graphics([_patch(first,t['bounds']) for t in visible_inventory(first)],
                           [_patch(second,t['bounds']) for t in visible_inventory(second)]):raise ValueError('equipment_inventory_changed')
    return new

def verify_equipment_effect(pending,after,after_png,review,*,reviewed_observation=None):
    """ROOT named graphic READ supplement; native names/stars stay untouched."""
    action=pending['action'];validate_equipment_action(action,None)
    reviewed = after if reviewed_observation is None else reviewed_observation
    keys={'source','request_id','input_request_id','match_id','resume_epoch','snapshot_id','capture_request_id',
          'frame_id','item_name','wear_slot','wear_roi_sha256','target_roi_sha256','same_target_card_verified','same_item_graphic_verified','wear_unobstructed','inventory_fully_visible','no_combination_or_replacement','findings'}
    if (not isinstance(review,dict) or set(review)!=keys or review['source']!='supervising_agent'
        or review['request_id']!=pending['request_id'] or review['input_request_id']!=pending['input_request_id']
        or review['match_id']!=pending['match_id'] or review['resume_epoch']!=pending['resume_epoch']
        or any(review[k]!=reviewed.get(k) for k in ('snapshot_id','capture_request_id','frame_id'))
        or review['item_name']!=action['equipment_read_review']['item_name']
        or type(review['wear_slot']) is not int or not 1<=review['wear_slot']<=3
        or any(review[k] is not True for k in ('wear_unobstructed','inventory_fully_visible','no_combination_or_replacement','same_item_graphic_verified','same_target_card_verified'))
        or not isinstance(review['findings'],str) or not review['findings'].strip()):
        raise ValueError('current_independent_target_wear_review_required')
    before=pending['before'];a=_image(pending['before_png'],before['snapshot_id']);b=_image(after_png,after['snapshot_id'])
    old=equipment_facts(before,action,pending['before_png']);slots,pop,res=_occupied_resources(after)
    if after.get('page')!='preparation' or after['fields'].get('stage')!=before['fields'].get('stage') or old['population']!=pop or old['resources']!=res:
        raise ValueError('equipment_effect_resources_or_page_changed')
    target_key=('board',action['destination']['row'],action['destination']['slot'])
    for key,slot in old['slots'].items():
        if not _compatible_slot(slot,slots[key]):raise ValueError('equipment_effect_roster_changed')
        x,y,r,bottom=slot['bounds']
        if key == target_key:
            # A completed drop can select/expand its current portrait. Target
            # continuity is the explicit current ROOT graphic READ, not fake
            # native identity. Other eighteen native card bodies remain exact.
            if hashlib.sha256(b[y:bottom,x:r].tobytes()).hexdigest()!=review['target_roi_sha256']:
                raise ValueError('ROOT_target_card_read_not_exact_current_ROI')
        elif not np.array_equal(a[y:bottom,x:r],b[y:bottom,x:r]):
            raise ValueError('equipment_effect_other_card_body_changed')
        if key!=target_key:
            if not _match_graphics([_patch(a,t,2) for t in wear_slots(a,slot)],[_patch(b,t,2) for t in wear_slots(b,slots[key])]):
                raise ValueError('other_character_equipment_changed')
    new_inventory=visible_inventory(b);old_inventory=visible_inventory(a)
    remaining=[_patch(a,t['bounds']) for t in old_inventory if t['slot']!=action['item']['slot']]
    if not _match_graphics(remaining,[_patch(b,t['bounds']) for t in new_inventory]):raise ValueError('equipment_effect_other_inventory_changed')
    old_wear=wear_slots(a,old['target']);new_wear=wear_slots(b,slots[target_key])
    if len(new_wear)!=len(old_wear)+1 or len(new_wear)>3 or review['wear_slot']>len(new_wear):raise ValueError('target_wear_increment_not_proved')
    added=new_wear[review['wear_slot']-1]
    x,y,r,bottom=added
    if hashlib.sha256(b[y:bottom,x:r].tobytes()).hexdigest()!=review['wear_roi_sha256']:
        raise ValueError('ROOT_wear_graphic_read_not_exact_current_ROI')
    # Inventory and equipped icons have distinct scales/frame ratios: actual
    # same-star RGB correlation .845. Identity is the explicit bound ROOT READ,
    # not a lowered global image threshold or a claim of native catalogue name.
    graphic_diagnostic=_same_graphic(_patch(a,old['item']['bounds']),_patch(b,added,2))
    if not _match_graphics([_patch(a,t,2) for t in old_wear],[_patch(b,t,2) for i,t in enumerate(new_wear,1) if i!=review['wear_slot']]):
        raise ValueError('target_old_equipment_changed_or_combined')
    return {'outcome':'wear_graphic_and_supervised_name_observed','origin':'supervising_agent',
        'item_name':review['item_name'],'destination':copy.deepcopy(action['destination']),
        'snapshot_id':after['snapshot_id'],'review_source_snapshot_id':reviewed['snapshot_id'],'machine_same_graphic_diagnostic':graphic_diagnostic,'graphic_identity_origin':'supervising_agent','native_identity_inferred':False,'input_resent':False}


def verify_archived_equipment(pending,before_png,after,after_png,receipts,origin,*,read_review=None):
    """Original stopped receipt fence; caller authenticates owner/archive/CAS."""
    result={'schema':'currency-wars-equipment-archive-verification/v1','status':'unknown','input_sent':False,
            'retry_allowed':False,'request_id':pending.get('request_id')}
    try:
        before=pending['before'];rid=pending['input_request_id'];prior=set(pending['prior_receipt_ids'])
        if (pending.get('schema')!='currency-wars-equipment-intent/v1'
                or pending.get('publication_attempted') is not True or pending.get('outcome')!='pending'
                or not prior<=receipts.keys() or before['capture_request_id'] not in prior
                or hashlib.sha256(before_png).hexdigest()!=before['snapshot_id']
                or hashlib.sha256(after_png).hexdigest()!=after['snapshot_id']
                or any(item.get('origin')!=origin or item.get('id')!=key or item['request'].get('id')!=key
                       or item['request'].get('chat_id')!=origin['chat_id'] for key,item in receipts.items())):
            raise ValueError('original_equipment_archive_scope_or_PNG_invalid')
        for observation in (before,after):
            meta=receipts[observation['capture_request_id']].get('result',{}).get('observation') or {}
            if meta.get('request_id')!=observation['capture_request_id'] or any(meta.get(k)!=observation[v]
                for k,v in (('snapshot_sha256','snapshot_id'),('frame_id','frame_id'),('captured_at','captured_at'))):
                raise ValueError('original_equipment_frame_receipt_unbound')
        facts=equipment_facts(before,pending['action'],pending['before_png'])
        expected=[{'type':'drag','args':facts['item']['point']+facts['target']['point']},{'type':'wait','args':[.7]}]
        remaining=set(receipts)-prior
        if not {rid,after['capture_request_id']}<=remaining or rid==after['capture_request_id']:
            raise ValueError('original_equipment_independent_after_missing')
        for key in remaining:
            item=receipts[key];actions=expected if key==rid else [{'type':'observe','args':[]}]
            request,response=item['request'],item.get('result') or {}
            if (request.get('kind')!='actions' or request.get('handoff') is not False or request.get('actions')!=actions
                    or response.get('id')!=key or response.get('ok') is not True or response.get('completed')!=actions
                    or response.get('input_attempted') is not (key==rid)
                    or response.get('attempted_actions')!=(expected[:1] if key==rid else [])):
                raise ValueError('original_equipment_archive_has_other_or_unknown_input')
        effect=verify_equipment_effect(pending,after,pending['after_png'],read_review)
        result.update(status='verified_original_effect',original_run_id=origin['run_id'],input_request_id=rid,
                      original_after_capture_request_id=after['capture_request_id'],after_snapshot_id=after['snapshot_id'],
                      effect=effect,receipt_ids_checked=sorted(remaining),native_identity_rewritten=False)
    except (KeyError,TypeError,ValueError,AttributeError,OSError) as exc:result['reason']=str(exc)
    return result
