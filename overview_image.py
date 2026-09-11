"""Pure snapshot renderer. No database, network, or Discord calls in workers."""
import io
import re
import time
import threading
from pathlib import Path
from collections import OrderedDict
from PIL import Image, ImageDraw, ImageFont

FONT=Path(__file__).parent/'assets/fonts/Overview.ttf'
CACHE=OrderedDict()
LOCK=threading.Lock()

def plain(text):
    return re.sub(r'[*_`\\]', '',str(text)).encode('ascii','replace').decode().replace('?',' ')

def render(snapshot):
    key=repr(snapshot)
    with LOCK:
        hit=CACHE.get(key)
        if hit and time.monotonic()-hit[0]<60:
            CACHE.move_to_end(key);return hit[1]
    tiles=snapshot['tiles'];rows=(len(tiles)+2)//3
    image=Image.new('RGB',(960,400+rows*180),'#101820');draw=ImageDraw.Draw(image)
    bitmap=None
    try:
        fonts={size:ImageFont.truetype(str(FONT),size) for size in (18,22,26,34,42)}
    except (ImportError,OSError):
        # Termux may ship Pillow without _imagingft. This embedded bitmap font
        # uses only _imaging, not FreeType or a phone-installed font.
        loader=getattr(ImageFont,'load_default_imagefont',ImageFont.load_default)
        bitmap=loader()
    def text(x,y,value,size=22,color='#e8f2f6',width=860):
        value=plain(value)
        font=bitmap if bitmap is not None else fonts[size]
        scale=size/11 if bitmap is not None else 1
        while value and draw.textlength(value,font=font)*scale>width:value=value[:-4]+'...' if len(value)>4 else value[:-1]
        if bitmap is None:
            draw.text((x,y),value,font=font,fill=color)
        elif value:
            bounds=font.getbbox(value)
            layer=Image.new('RGBA',(max(1,bounds[2]-bounds[0]),max(1,bounds[3]-bounds[1])),(0,0,0,0))
            ImageDraw.Draw(layer).text((-bounds[0],-bounds[1]),value,font=font,fill=color)
            layer=layer.resize((min(width,max(1,round(layer.width*scale))),max(1,round(layer.height*scale))),Image.Resampling.NEAREST)
            image.paste(layer,(x,y),layer)
    draw.rounded_rectangle((24,24,936,376+rows*180),radius=24,fill='#17232e',outline='#2d4452',width=2)
    text(48,42,'X SYSTEM  /  PERSONAL DASHBOARD  /  OV-IMG-2',18,'#41d9d0')
    text(48,70,'MY OVERVIEW',42)
    draw.rounded_rectangle((48,132,912,284),radius=16,fill='#20323e')
    text(68,146,'CURRENT GOAL',18,'#41d9d0')
    goal=plain(snapshot['goal']).splitlines()
    for index,line in enumerate(goal[:3]):text(68,176+index*29,line,22,width=820)
    text(48,300,snapshot['upgrade'],18,'#adc2cf')
    for index,tile in enumerate(tiles):
        x=48+(index%3)*294;y=350+(index//3)*180
        draw.rounded_rectangle((x,y,x+276,y+160),radius=16,fill='#20303c',outline='#344b5a')
        ready=tile[1].startswith('Ready ') and tile[1]!='Ready 0'
        accent='#61dbab' if ready else '#41d9d0'
        draw.rounded_rectangle((x+16,y+18,x+42,y+44),radius=7,outline=accent,width=2)
        text(x+23,y+19,str(index+1),18,accent,width=18)
        text(x+52,y+20,tile[0].upper(),18,'#adc2cf',width=212)
        text(x+16,y+64,tile[1],34,width=244)
        auxiliary='' if tile[2] in ('View recipes','View perks') else tile[2]
        text(x+16,y+114,auxiliary,22,'#adc2cf',width=244)
    text(48,350+rows*180,'Energy: last recorded. Open panels for exact totals.',18,'#8ea8ba')
    output=io.BytesIO();image.save(output,format='PNG',optimize=True);data=output.getvalue()
    with LOCK:
        CACHE[key]=(time.monotonic(),data);CACHE.move_to_end(key)
        while len(CACHE)>32:CACHE.popitem(last=False)
    return data
