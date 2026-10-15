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
    # Empty is a native positive supplied by the caller's full-roster contract,
    # never inferred from absent graphics on the animated board background.
    if card.get('status')=='empty':return []
    if card.get('status')=='unknown':raise ValueError('wear_card_occupancy_unknown')
    patch,quad,unused=_card_outline(rgb,card)
    x,y,r,b=card['bounds'];quad=quad+np.float32([x-24,y-24])
    bottom=quad[2:];edge=float(bottom[:,1].mean());center=float(bottom[:,0].mean())
    left=int(np.floor(quad[:,0].min()))-42;right=int(np.ceil(quad[:,0].max()))+42
    top=int(np.floor(bottom[:,1].min()))-3;end=int(np.ceil(bottom[:,1].max()))+50
    if left<0 or top<0 or right>rgb.shape[1] or end>rgb.shape[0]:
        raise ValueError('wear_row_outside_original_PNG')
    boxes=_squares(rgb,[left,top,right,end],maximum=3,minimum=24,largest=42)
    if (any(float(cv2.cvtColor(_patch(rgb,box,3),cv2.COLOR_RGB2GRAY).std())<8 for box in boxes)
            or any(not edge-3<=box[1]<=edge+8 for box in boxes)
            or boxes and (max(box[1] for box in boxes)-min(box[1] for box in boxes)>3
                or abs((boxes[0][0]+boxes[-1][2])/2-center)>6
                or any(b[0]-a[2]<0 or b[0]-a[2]>8 for a,b in zip(boxes,boxes[1:])))):
        raise ValueError('wear_graphics_not_single_card_attached_row')
    return boxes


def _rendering_equal(first, second, mask, *, peak, mean, tile):
    """Bound finite render residuals locally; correlation is not identity."""
    if first.shape != second.shape or not mask.any():return False
    delta=np.abs(first.astype(np.int16)-second.astype(np.int16))
    if delta[mask].max()>peak or delta[mask].mean()>mean:return False
    for rows, masks in zip(np.array_split(delta,8,axis=0),np.array_split(mask,8,axis=0)):
        for part, selected in zip(np.array_split(rows,8,axis=1),np.array_split(masks,8,axis=1)):
            if selected.any() and part[selected].mean()>tile:return False
    return True


def _same_visible_item(first, second):
    if first.shape != second.shape or min(first.shape[:2])<64:return False
    core=np.zeros(first.shape[:2],bool);core[6:-6,6:-6]=True
    # The complete square was independently parsed on both frames. Keep its
    # content and frame, with separate limited edge/illumination residuals.
    return (_rendering_equal(first,second,core,peak=6,mean=.20,tile=4)
        and _rendering_equal(first,second,~core,peak=32,mean=1.25,tile=6))


def _card_outline(rgb, slot):
    """Find a complete card frame near native geometry, never any screen ROI."""
    x,y,r,b=slot['bounds'];padding=24
    patch=rgb[y-padding:b+padding,x-padding:r+padding]
    if patch.shape[:2]!=(b-y+2*padding,r-x+2*padding):raise ValueError('card_frame_outside_original_PNG')
    edges=cv2.Canny(cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY),30,80)
    contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    nearby=cv2.dilate(edges,np.ones((3,3),np.uint8))
    candidates=[]
    def consider(quad):
        if (len(quad)!=4 or not cv2.isContourConvex(quad)
                or not .65*(r-x)*(b-y)<cv2.contourArea(quad)<1.6*(r-x)*(b-y)):return
        vertices=quad.reshape(4,2).astype(np.float32)
        center=vertices.mean(axis=0)
        if np.max(np.abs(center-np.array([(r-x)/2+padding,(b-y)/2+padding])))>padding:return
        # Order all four complete corners consistently. No portrait-only mask.
        top=vertices[np.argsort(vertices[:,1])[:2]];bottom=vertices[np.argsort(vertices[:,1])[2:]]
        top=top[np.argsort(top[:,0])];bottom=bottom[np.argsort(bottom[:,0])]
        ordered=np.array([top[0],top[1],bottom[1],bottom[0]],np.float32)
        width=(np.linalg.norm(ordered[1]-ordered[0])+np.linalg.norm(ordered[2]-ordered[3]))/2
        height=(np.linalg.norm(ordered[3]-ordered[0])+np.linalg.norm(ordered[2]-ordered[1]))/2
        if abs((height/width)/((b-y)/(r-x))-1)>.10:return
        if (min(np.linalg.norm(ordered[1]-ordered[0]),np.linalg.norm(ordered[2]-ordered[3]))<.75*(r-x)
                or min(ordered[3,1]-ordered[0,1],ordered[2,1]-ordered[1,1])<.85*(b-y)):return
        supported=True
        for start,end in zip(ordered,np.roll(ordered,-1,axis=0)):
            points=np.rint(start+(end-start)*np.linspace(.02,.98,60)[:,None]).astype(int)
            if np.mean(nearby[points[:,1],points[:,0]]>0)<.65:supported=False
        if supported:candidates.append((cv2.contourArea(quad),ordered))
    for contour in contours:
        # A role badge may notch an otherwise complete selection frame.
        outline=cv2.convexHull(contour)
        consider(cv2.approxPolyDP(outline,.02*cv2.arcLength(outline,True),True))
    if not candidates:
        # Three attached frames may join the outer edge contour. Independently
        # parse that complete centred row first, then separate only its edges
        # when locating the card. Raw pixels and all card/glyph checks stay intact.
        rough=_squares(rgb,[x-42,b-32,r+42,b+50],maximum=6,minimum=24,largest=42)
        rows=[]
        for box in rough:
            row=sorted([other for other in rough if abs(other[1]-box[1])<=3],key=lambda item:item[0])
            if (row not in rows and 1<=len(row)<=3
                    and max(item[1] for item in row)-min(item[1] for item in row)<=3
                    and abs((row[0][0]+row[-1][2])/2-(x+r)/2)<=12
                    and all(0<=bb[0]-aa[2]<=8 for aa,bb in zip(row,row[1:]))
                    and all(b-32<=item[1]<=b+10 for item in row)):
                rows.append(row)
        if len(rows)==1:
            separated=edges.copy()
            cutoff=min(box[1] for box in rows[0])-1-(y-padding)
            separated[max(0,cutoff):]=0
            parts,_=cv2.findContours(separated,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
            for part in parts:
                outline=cv2.convexHull(part)
                consider(cv2.approxPolyDP(outline,.02*cv2.arcLength(outline,True),True))
    if not candidates:raise ValueError('complete_native_card_frame_not_visible')
    candidates.sort(key=lambda v:v[0],reverse=True);quad=candidates[0][1]
    if any(np.max(np.abs(other.mean(axis=0)-quad.mean(axis=0)))>3
            or np.max(np.abs(other-quad))>3 and any(
                cv2.pointPolygonTest(quad,tuple(map(float,point)),True)<-1.5 for point in other)
            for unused,other in candidates[1:]):
        raise ValueError('native_card_frame_ambiguous')
    mask=np.zeros(patch.shape[:2],np.uint8);cv2.fillConvexPoly(mask,quad.astype(np.int32),1)
    return patch,quad,mask


def _same_occupied_card(first, second, slot):
    """Occupied portrait/role/star/frame continuity; no native identity inference."""
    try:
        x,y,r,b=slot['bounds']
        if slot['row']=='bench':
            # Native bench geometry includes a six-pixel outer frame. Keep
            # every pixel, but do not use its AA distribution as portrait ink.
            a=first[y:b,x:r];z=second[y:b,x:r]
            core=np.zeros(a.shape[:2],bool);core[6:-6,6:-6]=True
            if (not _rendering_equal(a,z,core,peak=8,mean=.10,tile=.50)
                    or not _rendering_equal(a,z,~core,peak=8,mean=.10,tile=12)):
                return False
            # Complete native role field: preserve the actual white glyphs,
            # including any glyph ink touching the frame band independently.
            one,two=a[:47,-42:],z[:47,-42:]
            masks=[]
            for crop in (one,two):
                hsv=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)
                masks.append((hsv[:,:,1]<60)&(hsv[:,:,2]>200))
            if (not np.array_equal(*masks) or np.count_nonzero(masks[0])<24
                    or not _rendering_equal(one,two,masks[0],peak=4,mean=.10,tile=.50)):
                return False
            one,two=a[-49:-5,27:-27],z[-49:-5,27:-27]
            return _rendering_equal(one,two,np.ones(one.shape[:2],bool),peak=4,mean=.10,tile=.50)
        a,quad,mask=_card_outline(first,slot);z,new_quad,new_mask=_card_outline(second,slot)
        if np.max(np.abs(quad-new_quad))>1:return False
        # Six-pixel frame band separates animated outer antialiasing from the
        # actual portrait. It is still checked, including selection/quality.
        core=cv2.erode(mask,np.ones((13,13),np.uint8)).astype(bool)
        rim=(mask|new_mask).astype(bool)&~core
        if (not _rendering_equal(a,z,core,peak=4,mean=.10,tile=.50)
                or not _rendering_equal(a,z,rim,peak=48,mean=.75,tile=12)):return False
        # Explicit full role and star fields also include glyph parts touching
        # the frame band. Same source warp on both images does not align away a
        # moved badge/star or changed portrait; a new selection shape fails above.
        warp=cv2.getPerspectiveTransform(quad,np.float32([[0,0],[127,0],[127,143],[0,143]]))
        aa=cv2.warpPerspective(a,warp,(128,144));zz=cv2.warpPerspective(z,warp,(128,144))
        for lx,ly,rx,by in ((99,7,124,39),(16,115,112,139)):
            one,two=aa[ly:by,lx:rx],zz[ly:by,lx:rx]
            if not _rendering_equal(one,two,np.ones(one.shape[:2],bool),peak=4,mean=.10,tile=.50):return False
        return True
    except (ValueError,cv2.error,IndexError):return False

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
    for key,slot in old['slots'].items():
        if slot['status']=='occupied' and not _same_occupied_card(first,second,slot):
            raise ValueError('equipment_current_occupied_card_structure_changed')
        if not _match_graphics([_patch(first,t,2) for t in wear_slots(first,slot)],
                               [_patch(second,t,2) for t in wear_slots(second,new['slots'][key])]):
            raise ValueError('equipment_current_character_wear_changed')
    for key,digest in (('item','item_roi_sha256'),('target','target_roi_sha256')):
        box=old[key]['bounds'];x,y,r,b=box
        if box!=new[key]['bounds'] or hashlib.sha256(first[y:b,x:r].tobytes()).hexdigest()!=review[digest]:
            raise ValueError('equipment_original_ROI_digest_or_geometry_changed')
        if not (_same_visible_item(first[y:b,x:r],second[y:b,x:r]) if key=='item'
                else _same_occupied_card(first,second,old['target'])):
            raise ValueError('equipment_current_source_or_target_structure_changed')
    if not _match_graphics([_patch(first,t['bounds']) for t in visible_inventory(first)],
                           [_patch(second,t['bounds']) for t in visible_inventory(second)]):raise ValueError('equipment_inventory_changed')
    return new

def verify_equipment_effect(pending,after,after_png,review,*,reviewed_observation=None,reviewed_png=None):
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
    reviewed_rgb=b
    independent_review=reviewed.get('snapshot_id')!=after.get('snapshot_id')
    if independent_review:
        if reviewed_png is None:raise ValueError('ROOT_original_reviewed_PNG_required_for_fresh_continuation')
        reviewed_rgb=_image(reviewed_png,reviewed['snapshot_id'])
        # Authenticate the literal original ROOT digests against the exact
        # reviewed after frame. A later capture never receives invented digests.
        verify_equipment_effect(pending,reviewed,reviewed_png,review)
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
            # native identity. Other occupied cards retain portrait/role/star/frame structure;
            # native empty positives do not compare an animated scene background.
            if hashlib.sha256(reviewed_rgb[y:bottom,x:r].tobytes()).hexdigest()!=review['target_roi_sha256']:
                raise ValueError('ROOT_target_card_read_not_exact_reviewed_ROI')
            if independent_review and not _same_occupied_card(reviewed_rgb,b,slots[key]):
                raise ValueError('ROOT_reviewed_target_card_structure_changed_in_fresh_capture')
        elif slot['status']=='occupied' and not _same_occupied_card(a,b,slot):
            raise ValueError('equipment_effect_other_occupied_card_structure_changed')
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
    reviewed_wear=wear_slots(reviewed_rgb,slots[target_key])
    if (len(reviewed_wear)!=len(new_wear) or reviewed_wear[review['wear_slot']-1]!=added):
        raise ValueError('ROOT_reviewed_wear_slot_geometry_changed')
    if hashlib.sha256(reviewed_rgb[y:bottom,x:r].tobytes()).hexdigest()!=review['wear_roi_sha256']:
        raise ValueError('ROOT_wear_graphic_read_not_exact_reviewed_ROI')
    if independent_review:
        one,two=reviewed_rgb[y:bottom,x:r],b[y:bottom,x:r]
        core=np.zeros(one.shape[:2],bool);core[3:-3,3:-3]=True
        if (not _rendering_equal(one,two,core,peak=6,mean=.20,tile=4)
                or not _rendering_equal(one,two,~core,peak=32,mean=1.25,tile=6)):
            raise ValueError('ROOT_reviewed_new_wear_graphic_changed_in_fresh_capture')
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


def plain_equipment_tooltip(observation):
    """A bounded right-side equipment tooltip, not arbitrary Escape navigation."""
    if observation.get('page') != 'preparation':
        raise ValueError('equipment_tooltip_not_preparation')
    contract = observation.get('read_contract', {})
    if contract.get('version') != 2 or contract.get('effective_scope') != 'full' or contract.get('unread') != []:
        raise ValueError('equipment_tooltip_not_full_read')
    rows = observation.get('rows', [])
    categories = [row for row in rows if row.get('text') in ('简易装备', '进阶装备')]
    if len(categories) != 1 or categories[0].get('confidence', 0) < .90:
        raise ValueError('plain_equipment_tooltip_category_not_unique')
    category = categories[0]; x,y,r,b = category['box']
    if not (1200 <= x < r <= 1780 and 100 <= y < b <= 700 and b-y >= 15):
        raise ValueError('equipment_tooltip_category_outside_right_panel')
    titles = [row for row in rows if row.get('confidence', 0) >= .90
        and 2 <= len(row.get('text', '')) <= 20
        and abs(row['box'][0]-x) <= 15 and y-65 <= row['box'][1]
        and row['box'][3] <= y and 15 <= row['box'][3]-row['box'][1] <= 50]
    if (len(titles) != 1 or any(row.get('text') in ('确认', '取消', '挑战结束', '退出战斗', '请选择1个')
                               and row.get('confidence', 0) >= .72 for row in rows)):
        raise ValueError('equipment_tooltip_title_or_modal_ambiguous')
    return {'title':copy.deepcopy(titles[0]), 'category':copy.deepcopy(category)}


def _inspection_resources(observation):
    # Tooltip may cover an empty board slot. It must not cover/mutate these HUD
    # facts; full roster is checked only after the independent closed capture.
    hud = observation.get('semantic', {}).get('player_hud') or {}
    coins = observation.get('semantic', {}).get('coins') or {}
    snapshot = observation.get('snapshot_id')
    from currency_wars_deployment import _population
    if (hud.get('snapshot_id') != snapshot or hud.get('origin') != 'native_player_hud'
            or hud.get('actor') != 'player' or type(hud.get('level')) is not int
            or str(hud['level']) != str(observation.get('fields', {}).get('level'))
            or not isinstance(hud.get('xp'), list) or len(hud['xp']) != 2
            or any(type(v) is not int or v < 0 for v in hud['xp']) or hud['xp'][0] > hud['xp'][1]
            or coins.get('snapshot_id') != snapshot or type(coins.get('value')) is not int
            or coins['value'] < 0 or not .90 <= coins.get('confidence', 0) <= 1.
            or coins.get('bounds') != [1613,892,1687,950]):
        raise ValueError('inspection_resources_not_native_bound')
    return (observation['fields'].get('stage'), hud['level'], hud['xp'], coins['value'], _population(observation))


def _same_tool_counts(first, second):
    # Complete fixed counter fields, separate from animated metal/background.
    # This proves unchanged glyphs, not a fabricated native numeric value.
    for x,y,r,b in ((1784,198,1809,228),(1862,198,1888,228)):
        a,bgr = first[y:b,x:r], second[y:b,x:r]
        masks=[]
        for crop in (a,bgr):
            hsv=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)
            mask=(hsv[:,:,1]<60)&(hsv[:,:,2]>200)
            yy,xx=np.where(mask)
            if len(xx)<12 or min(xx.min(), yy.min(), crop.shape[1]-1-xx.max(), crop.shape[0]-1-yy.max())<2:
                return False
            masks.append(mask)
        if not np.array_equal(*masks) or not np.array_equal(a[masks[0]],bgr[masks[1]]):return False
    return True


def verify_inspection_roundtrip(before, before_png, tooltip, tooltip_png, closed, closed_png):
    """Read-only qualification after real click/Escape; caller seals receipts."""
    facts=plain_equipment_tooltip(tooltip)
    images=[_image(path,obs['snapshot_id']) for obs,path in
            ((before,before_png),(tooltip,tooltip_png),(closed,closed_png))]
    if (len({obs.get('capture_request_id') for obs in (before,tooltip,closed)}) != 3
            or len({obs.get('frame_id') for obs in (before,tooltip,closed)}) != 3
            or _inspection_resources(before) != _inspection_resources(tooltip)
            or _inspection_resources(before) != _inspection_resources(closed)
            or any(row.get('text') in ('简易装备','进阶装备','确认','取消','挑战结束','退出战斗','请选择1个')
                   and row.get('confidence',0)>=.72 for obs in (before,closed) for row in obs.get('rows',[]))):
        raise ValueError('inspection_roundtrip_resources_or_closed_page_changed')
    old,pop,resources=_occupied_resources(before)
    new,new_pop,new_resources=_occupied_resources(closed)
    if pop != new_pop or resources != new_resources or any(not _compatible_slot(slot,new[k]) for k,slot in old.items()):
        raise ValueError('inspection_roundtrip_roster_changed')
    a,_,c=images
    for key,slot in old.items():
        if slot['status']=='occupied' and not _same_graphic(_patch(a,slot['bounds']),_patch(c,new[key]['bounds'])):
            raise ValueError('inspection_roundtrip_card_graphic_changed')
        if not _match_graphics([_patch(a,t,2) for t in wear_slots(a,slot)],
                               [_patch(c,t,2) for t in wear_slots(c,new[key])]):
            raise ValueError('inspection_roundtrip_character_equipment_changed')
    if (not _match_graphics([_patch(a,t['bounds']) for t in visible_inventory(a)],
                            [_patch(c,t['bounds']) for t in visible_inventory(c)])
            or not _same_tool_counts(a,c)):
        raise ValueError('inspection_roundtrip_inventory_or_tool_count_changed')
    return {'outcome':'verified_equipment_information_roundtrip', 'origin':'original_receipt_and_native_graphic_continuity',
        'before_snapshot_id':before['snapshot_id'],'tooltip_snapshot_id':tooltip['snapshot_id'],
        'closed_snapshot_id':closed['snapshot_id'],'closed_capture_request_id':closed['capture_request_id'],
        'closed_frame_id':closed['frame_id'],'tooltip_title':facts['title']['text'],
        'native_identity_inferred':False,'phase_completion':False,'input_resent':False}
