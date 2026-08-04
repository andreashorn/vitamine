import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const colors = {
  ink: '#172b2d',
  deep: '#10251f',
  mint: '#dceee8',
  mintLight: '#eff8f5',
  green: '#176b5b',
  bright: '#72cfae',
  gold: '#f5bb54',
  warm: '#efe9df',
  muted: '#78908c',
  white: '#ffffff',
};

const sceneStarts = [0, 240, 540, 900, 1200, 1530, 1830, 2040];

const clamp = (value: number) => Math.max(0, Math.min(1, value));

const progress = (frame: number, start: number, duration: number) =>
  clamp((frame - start) / duration);

const enter = (frame: number, start: number, duration = 22) => {
  const {fps} = useVideoConfig();
  return spring({frame: Math.max(0, frame - start), fps, config: {damping: 200, mass: 0.8}});
};

const fade = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, start + 16, end - 16, end], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.ease),
  });

const Wordmark: React.FC<{light?: boolean}> = ({light = false}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 13, color: light ? colors.white : colors.ink, fontSize: 34, fontWeight: 800, letterSpacing: '-0.06em'}}>
    <span style={{display: 'grid', placeItems: 'center', width: 35, height: 35, borderRadius: '50% 50% 42% 42%', background: colors.gold, color: colors.deep, fontSize: 23, fontWeight: 900}}>V</span>
    VitaMine
  </div>
);

const Headline: React.FC<{eyebrow?: string; title: React.ReactNode; body?: string; light?: boolean; align?: 'left' | 'center'}> = ({eyebrow, title, body, light = false, align = 'left'}) => (
  <div style={{maxWidth: 840, textAlign: align, color: light ? colors.white : colors.ink}}>
    {eyebrow ? <div style={{marginBottom: 20, color: light ? colors.bright : colors.green, fontSize: 20, fontWeight: 800, letterSpacing: '0.14em', textTransform: 'uppercase'}}>{eyebrow}</div> : null}
    <div style={{fontSize: 78, fontWeight: 760, letterSpacing: '-0.065em', lineHeight: 0.95}}>{title}</div>
    {body ? <div style={{marginTop: 28, color: light ? '#b9d1c8' : '#5d726f', fontSize: 29, lineHeight: 1.35}}>{body}</div> : null}
  </div>
);

const Frame: React.FC<{children: React.ReactNode; background: string; opacity?: number}> = ({children, background, opacity = 1}) => (
  <AbsoluteFill style={{background, opacity, overflow: 'hidden', fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'}}>{children}</AbsoluteFill>
);

const Topline: React.FC<{light?: boolean; label: string}> = ({light, label}) => (
  <div style={{position: 'absolute', top: 58, left: 80, right: 80, display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
    <Wordmark light={light} />
    <div style={{color: light ? '#b7d5cb' : colors.muted, fontSize: 18, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase'}}>{label}</div>
  </div>
);

const RequestCard: React.FC<{x: number; y: number; label: string; text: string; visible: number}> = ({x, y, label, text, visible}) => (
  <div style={{position: 'absolute', left: `${x}%`, top: `${y}%`, width: 278, padding: '18px 20px', borderRadius: 16, background: 'rgba(255,255,255,.94)', boxShadow: '0 18px 45px rgba(23,45,41,.15)', opacity: visible, transform: `translateY(${(1 - visible) * -40}px) rotate(${(x - 50) / 18}deg)`}}>
    <div style={{color: colors.green, fontSize: 14, fontWeight: 800, letterSpacing: '.1em', textTransform: 'uppercase'}}>{label}</div>
    <div style={{marginTop: 8, color: colors.ink, fontSize: 17, fontWeight: 700, lineHeight: 1.25}}>{text}</div>
  </div>
);

const ProblemScene: React.FC<{frame: number}> = ({frame}) => {
  const local = frame - sceneStarts[1];
  const opacity = fade(frame, sceneStarts[1], sceneStarts[2]);
  const requests = [
    ['Promotion', 'A concise CV—with mentoring successes.'],
    ['Funding call', 'A biosketch in the required format.'],
    ['Prize committee', 'A two-page CV in French.'],
    ['University website', 'A short biography and recent papers.'],
  ];
  return <Frame background={colors.mint} opacity={opacity}>
    <Topline label="One source of truth" />
    <div style={{position: 'absolute', left: 100, top: 255}}><Headline title={<>Stop retelling<br />your career.</>} body="Every opportunity asks for a different version of the same academic record." /></div>
    <div style={{position: 'absolute', left: 1030, top: 190, width: 770, height: 720, borderRadius: 48, background: 'linear-gradient(145deg,#f5fbf8,#d0e6df)', boxShadow: 'inset 0 0 100px rgba(57,124,106,.12)'}}>
      {requests.map(([label, text], index) => <RequestCard key={label} x={[7, 53, 14, 52][index]} y={[8, 24, 50, 68][index]} label={label} text={text} visible={enter(local, index * 19 + 6)} />)}
      <div style={{position: 'absolute', left: 315, bottom: 45, width: 170, height: 245, border: `5px solid ${colors.ink}`, borderRadius: '28px 28px 42px 42px', background: `linear-gradient(140deg,${colors.gold},#e2931c)`, transform: `translateY(${(1 - enter(local, 96)) * 70}px) rotate(-8deg)`, opacity: enter(local, 96), boxShadow: '0 22px 45px rgba(36,71,61,.22)'}}>
        <div style={{position: 'absolute', top: -30, left: 42, width: 76, height: 30, borderRadius: '8px 8px 2px 2px', background: colors.ink}} />
        <div style={{position: 'absolute', inset: '56px 15px 45px', display: 'grid', placeItems: 'center', borderRadius: 13, background: colors.white, color: colors.ink, fontSize: 27, fontWeight: 850, letterSpacing: '-.08em'}}>VitaMine</div>
      </div>
    </div>
  </Frame>;
};

const ImportScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[2];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[3]);
  const chips = ['47 publications', '12 positions', '18 grants', '26 invited talks', '9 mentees', '7 awards'];
  return <Frame background={colors.deep} opacity={opacity}>
    <Topline light label="Effortless beginnings" />
    <div style={{position: 'absolute', left: 100, top: 290}}><Headline light eyebrow="Start with the CV you have" title={<>One record.<br />Ready to grow.</>} body="VitaMine extracts the details, organizes them, and keeps the database portable." /></div>
    <div style={{position: 'absolute', left: 1060, top: 170, width: 700, height: 760}}>
      <div style={{position: 'absolute', left: 0, top: 155, width: 270, height: 350, display: 'grid', placeItems: 'center', border: '3px dashed rgba(130,212,184,.65)', borderRadius: 28, color: '#b9d9ce', fontSize: 21, fontWeight: 700, opacity: enter(local, 8)}}>Drop your CV here</div>
      <div style={{position: 'absolute', left: 70, top: 35, width: 235, height: 300, borderRadius: 10, padding: 32, background: colors.white, color: colors.ink, transform: `translate(${interpolate(progress(local, 4, 120), [0, 1], [-30, 255])}px,${interpolate(progress(local, 4, 120), [0, 1], [-120, 130])}px) scale(${interpolate(progress(local, 100, 60), [0, 1], [1, .65])})`, opacity: 1 - progress(local, 120, 55), boxShadow: '0 28px 70px rgba(0,0,0,.32)'}}>
        <div style={{display: 'grid', placeItems: 'center', width: 48, height: 48, borderRadius: 7, background: '#2b579a', color: colors.white, fontWeight: 900}}>W</div><div style={{marginTop: 24, fontWeight: 800}}>academic-cv.docx</div><div style={{height: 7, marginTop: 25, borderRadius: 9, background: '#dbe4e1'}} /><div style={{height: 7, marginTop: 12, width: '74%', borderRadius: 9, background: '#dbe4e1'}} /><div style={{height: 7, marginTop: 12, borderRadius: 9, background: '#dbe4e1'}} />
      </div>
      <div style={{position: 'absolute', right: 55, top: 225, display: 'grid', placeItems: 'center', width: 235, height: 235, border: '2px solid rgba(130,212,184,.45)', borderRadius: '50%', background: 'rgba(130,212,184,.1)', boxShadow: '0 0 85px rgba(85,199,157,.24)', color: colors.white, transform: `scale(${interpolate(progress(local, 108, 70), [0, 1], [.75, 1])})`}}><div style={{textAlign: 'center'}}><div style={{fontSize: 52}}>◫</div><b style={{fontSize: 28}}>.vitamine</b><div style={{marginTop: 7, color: '#aecbc1', fontSize: 15}}>your portable database</div></div></div>
      {chips.map((chip, index) => <div key={chip} style={{position: 'absolute', left: [10, 340, 435, 15, 290, 430][index], top: [585, 70, 530, 665, 625, 680][index], border: '1px solid rgba(255,255,255,.18)', borderRadius: 999, padding: '11px 16px', background: 'rgba(255,255,255,.09)', color: colors.white, fontSize: 16, fontWeight: 700, opacity: enter(local, 145 + index * 11), transform: `translateY(${(1 - enter(local, 145 + index * 11)) * 28}px)`}}>{chip}</div>)}
    </div>
  </Frame>;
};

const SyncScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[3];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[4]);
  const nodes = [{x: 160, y: 430, text: 'iD', color: '#6aaf38'}, {x: 520, y: 90, text: 'Scholar', color: '#4774b8'}, {x: 875, y: 470, text: 'Z', color: '#aa3330'}, {x: 480, y: 755, text: 'OpenAlex', color: '#7765a7'}];
  const links = ['M224 454 C320 415 390 430 480 470', 'M565 156 C550 260 550 330 550 420', 'M886 510 C790 470 704 475 620 500', 'M532 758 C540 665 545 608 550 590'];
  return <Frame background={colors.mint} opacity={opacity}>
    <Topline label="Always current" />
    <div style={{position: 'absolute', left: 100, top: 320}}><Headline eyebrow="Your academic ecosystem" title={<>Let your record<br />stay in motion.</>} body="Connect the scholarly services you already trust." /></div>
    <div style={{position: 'absolute', left: 960, top: 90, width: 860, height: 900}}>
      <svg viewBox="0 0 1100 900" width="100%" height="100%" style={{overflow: 'visible'}}>
        <circle cx="550" cy="500" r="190" fill="none" stroke="rgba(23,107,91,.18)" strokeWidth="2" /><circle cx="550" cy="500" r="360" fill="none" stroke="rgba(23,107,91,.18)" strokeWidth="2" />
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={colors.green} /></marker></defs>
        {links.map((d, index) => { const visible = progress(local, 20 + index * 28, 80); return <path key={d} d={d} fill="none" stroke={colors.green} strokeWidth="9" strokeLinecap="round" strokeDasharray="1" pathLength="1" strokeDashoffset={1 - visible} markerEnd="url(#arrow)" />; })}
      </svg>
      <div style={{position: 'absolute', left: 425, top: 375, display: 'grid', placeItems: 'center', width: 250, height: 250, borderRadius: '50%', background: colors.white, boxShadow: '0 24px 60px rgba(23,67,58,.16)', transform: `scale(${enter(local, 0)})`}}><div style={{textAlign: 'center', color: colors.green, fontSize: 50}}>▤<div style={{marginTop: 8, color: colors.ink, fontSize: 20, fontWeight: 800}}>Your living CV</div></div></div>
      {nodes.map((node, index) => <div key={node.text} style={{position: 'absolute', left: node.x, top: node.y, display: 'grid', placeItems: 'center', width: node.text === 'OpenAlex' ? 130 : 104, height: node.text === 'OpenAlex' ? 130 : 104, borderRadius: '50%', background: colors.white, boxShadow: '0 16px 40px rgba(23,67,58,.14)', color: node.color, fontSize: node.text.length > 3 ? 17 : 30, fontWeight: 800, opacity: enter(local, 13 + index * 20), transform: `scale(${enter(local, 13 + index * 20)})`}}>{node.text}</div>)}
    </div>
  </Frame>;
};

const PromptScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[4];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[5]);
  const text = 'Keep the CV to two pages. Highlight recent first- and last-authorships. Use APA 7 and include DOI links.';
  const typed = text.slice(0, Math.floor(text.length * progress(local, 28, 205)));
  return <Frame background={colors.deep} opacity={opacity}>
    <Topline light label="Prompt-guided export" />
    <div style={{position: 'absolute', left: 100, top: 315}}><Headline light eyebrow="Simply describe what you need" title={<>The right CV.<br />In moments.</>} body="Format, focus, language, citation style—put it in plain words." /></div>
    <div style={{position: 'absolute', left: 970, top: 235, width: 790, borderRadius: 25, overflow: 'hidden', background: '#fcfdfd', color: colors.ink, boxShadow: '0 38px 100px rgba(0,0,0,.35)', transform: `translateY(${(1 - enter(local, 0)) * 70}px) rotate(-2deg)`, opacity: enter(local, 0)}}>
      <div style={{height: 66, display: 'flex', alignItems: 'center', gap: 10, padding: '0 22px', borderBottom: '1px solid #dee8e6', background: '#edf2f1'}}><i style={{width: 12, height: 12, borderRadius: '50%', background: '#e88976'}} /><i style={{width: 12, height: 12, borderRadius: '50%', background: '#e9c46b'}} /><i style={{width: 12, height: 12, borderRadius: '50%', background: '#78bd99'}} /><span style={{marginLeft: 12, color: '#657b77', fontWeight: 700}}>Describe your export</span></div>
      <div style={{minHeight: 315, padding: 42, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 26, lineHeight: 1.5}}>{typed}<span style={{display: 'inline-block', width: 3, height: 28, marginLeft: 5, background: colors.green, verticalAlign: -4}} /></div>
      <div style={{display: 'flex', justifyContent: 'space-between', padding: '20px 27px', borderTop: '1px solid #e1ebea', color: '#657b77', fontSize: 17}}><span>Academic · APA 7</span><b style={{borderRadius: 8, padding: '11px 16px', background: colors.green, color: colors.white}}>Generate CV ↗</b></div>
    </div>
  </Frame>;
};

const ProfileScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[5];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[6]);
  return <Frame background={colors.warm} opacity={opacity}>
    <Topline label="A living academic profile" />
    <div style={{position: 'absolute', left: 100, top: 315}}><Headline eyebrow="Share the work behind the CV" title={<>A profile that<br />keeps itself current.</>} body="Host it with VitaMine, or embed selected content wherever your work lives." /></div>
    <div style={{position: 'absolute', left: 960, top: 185, width: 805, height: 675, borderRadius: 24, overflow: 'hidden', background: colors.white, boxShadow: '0 35px 90px rgba(86,64,40,.2)', transform: `translateY(${(1 - enter(local, 0)) * 70}px)`, opacity: enter(local, 0)}}>
      <div style={{height: 57, display: 'flex', alignItems: 'center', gap: 12, padding: '0 20px', borderBottom: '1px solid #ece9e3', background: '#f7f5f1'}}><i style={{width: 11, height: 11, borderRadius: '50%', background: '#d4c9ba'}} /><div style={{flex: 1, borderRadius: 6, padding: '7px', background: colors.white, color: '#8b847a', fontSize: 14, textAlign: 'center'}}>vitamine.cloud/a-researcher</div></div>
      <div style={{padding: '30px 38px'}}>
        <div style={{display: 'flex', borderBottom: '1px solid #edf0ee', paddingBottom: 20, color: '#71817d', fontSize: 15, fontWeight: 700}}><b style={{marginRight: 'auto', color: colors.ink, fontSize: 23}}>AR<span style={{color: colors.green}}>.</span></b><span>About</span><span style={{marginLeft: 27}}>Research</span><span style={{marginLeft: 27}}>Publications</span></div>
        <div style={{display: 'flex', gap: 28, padding: '30px 0 24px', opacity: enter(local, 24), transform: `translateY(${(1 - enter(local, 24)) * 18}px)`}}><div style={{width: 116, height: 130, borderRadius: '58px 58px 16px 16px', background: '#cfe6de', display: 'grid', placeItems: 'center', color: colors.deep, fontSize: 55}}>◉</div><div><div style={{color: colors.green, fontSize: 14, fontWeight: 800, letterSpacing: '.08em'}}>PROFESSOR OF SYSTEMS NEUROSCIENCE</div><div style={{marginTop: 8, fontSize: 34, fontWeight: 800, letterSpacing: '-.05em'}}>Alex Researcher</div><div style={{marginTop: 9, color: '#6f7b78', fontSize: 17}}>University of VitaMine</div><div style={{marginTop: 20, display: 'flex', gap: 8}}>{['Cognition', 'Networks', 'Clinical'].map((tag) => <span key={tag} style={{borderRadius: 999, padding: '7px 10px', background: '#e8f1ee', color: colors.green, fontSize: 13, fontWeight: 700}}>{tag}</span>)}</div></div></div>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderTop: '1px solid #e5e8e7', borderBottom: '1px solid #e5e8e7', padding: '18px 0', opacity: enter(local, 50)}}>{[['82', 'Publications'], ['4,218', 'Citations'], ['32', 'Collaborators'], ['11', 'Mentees']].map(([number, label]) => <div key={label}><b style={{fontSize: 27}}>{number}</b><span style={{display: 'block', marginTop: 4, color: '#6f7b78', fontSize: 13}}>{label}</span></div>)}</div>
      </div>
    </div>
  </Frame>;
};

const InsightScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[6];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[7]);
  const graph = 'M90 410 C210 390 270 340 365 350 S520 310 620 250 S790 280 900 120';
  return <Frame background="#e2e8f0" opacity={opacity}>
    <Topline label="See the whole picture" />
    <div style={{position: 'absolute', left: 100, top: 315}}><Headline eyebrow="A record you can understand" title={<>The work.<br />The reach. The people.</>} body="Make your next step with a clearer view of your academic story." /></div>
    <div style={{position: 'absolute', left: 1010, top: 250, width: 730, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22}}>
      {[['4,218', 'Citations'], ['82', 'Publications']].map(([number, label], index) => <div key={label} style={{padding: 31, borderRadius: 22, background: 'rgba(255,255,255,.84)', boxShadow: '0 18px 48px rgba(48,64,78,.1)', opacity: enter(local, index * 10), transform: `translateY(${(1 - enter(local, index * 10)) * 24}px)`}}><span style={{color: '#6d7d85', fontWeight: 700}}>{label}</span><b style={{display: 'block', marginTop: 14, fontSize: 58, letterSpacing: '-.07em'}}>{number}</b><small style={{color: colors.green, fontWeight: 700}}>Growing with your work</small></div>)}
      <div style={{gridColumn: '1 / -1', padding: 32, borderRadius: 22, background: 'rgba(255,255,255,.84)', boxShadow: '0 18px 48px rgba(48,64,78,.1)', opacity: enter(local, 22)}}><span style={{color: '#6d7d85', fontWeight: 700}}>Citations over time</span><svg viewBox="0 0 1000 470" width="100%" height="290" style={{marginTop: 20, overflow: 'visible'}}><path d={`${graph} L900 450 L90 450 Z`} fill="rgba(114,207,174,.18)" /><path d={graph} fill="none" stroke={colors.green} strokeWidth="12" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress(local, 30, 135)} /></svg></div>
    </div>
  </Frame>;
};

const ClosingScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[7];
  const opacity = interpolate(frame, [start, start + 20], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <Frame background={colors.mint} opacity={opacity}>
    <div style={{position: 'absolute', inset: 0, background: 'radial-gradient(circle at 50% 44%,rgba(255,255,255,.9),transparent 32%)'}} />
    <div style={{position: 'absolute', top: 260, left: 0, right: 0, display: 'grid', placeItems: 'center', textAlign: 'center', transform: `translateY(${(1 - enter(frame, start + 8)) * 35}px)`, opacity: enter(frame, start + 8)}}><Wordmark /><div style={{marginTop: 45, color: colors.ink, fontSize: 88, fontWeight: 800, letterSpacing: '-.07em', lineHeight: .92}}>One career.<br />Every format.</div><div style={{marginTop: 28, color: '#58706b', fontSize: 29}}>Ready for what comes next.</div><div style={{marginTop: 50, borderRadius: 999, padding: '17px 28px', background: colors.green, color: colors.white, fontSize: 21, fontWeight: 800}}>vitamine.cloud</div></div>
  </Frame>;
};

export const PromoFilm: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill>
    <Audio src={staticFile('voiceover.m4a')} volume={0.95} />
    <Frame background={colors.mint} opacity={fade(frame, sceneStarts[0], sceneStarts[1])}>
      <Topline label="Academic CV manager" />
      <div style={{position: 'absolute', top: 290, left: 100, zIndex: 2, transform: `translateY(${(1 - enter(frame, 5)) * 45}px)`, opacity: enter(frame, 5)}}><Headline eyebrow="One career. Every format." title={<>Your academic record,<br />ready for what comes next.</>} body="VitaMine makes the CV you already have useful everywhere it needs to be." /></div>
      <div style={{position: 'absolute', right: 80, bottom: 40, width: 840, height: 720, borderRadius: '50% 50% 0 0', background: 'linear-gradient(140deg,#b9dfd1,#e6f4ef)', transform: `translateY(${(1 - enter(frame, 20)) * 100}px)`}}><div style={{position: 'absolute', left: 215, top: 145, width: 375, height: 440, borderRadius: '30px 30px 18px 18px', background: colors.white, boxShadow: '0 35px 80px rgba(31,91,74,.18)', transform: 'rotate(6deg)'}}><div style={{padding: 43, color: colors.ink, fontWeight: 800}}>CURRICULUM VITAE<div style={{height: 9, marginTop: 42, borderRadius: 9, background: '#dce7e3'}} /><div style={{height: 9, marginTop: 16, borderRadius: 9, background: '#dce7e3'}} /><div style={{height: 9, marginTop: 16, width: '65%', borderRadius: 9, background: '#dce7e3'}} /></div></div></div>
    </Frame>
    <ProblemScene frame={frame} />
    <ImportScene frame={frame} />
    <SyncScene frame={frame} />
    <PromptScene frame={frame} />
    <ProfileScene frame={frame} />
    <InsightScene frame={frame} />
    <ClosingScene frame={frame} />
  </AbsoluteFill>;
};
