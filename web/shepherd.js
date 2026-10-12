/* Shared audio bands and the 德牧 watch-face theme. No raVe DSP changes. */
const AudioBands = Object.freeze({
  low: [20, 250], mid: [250, 1000], high: [1000, Infinity],
  analyze(data, sampleRate, fftSize, minDb, maxDb) {
    const sum = {low:0,mid:0,high:0}, count = {low:0,mid:0,high:0};
    let bassPower=0,bassCount=0,bassPeak=0;
    let weighted = 0, lowMidWeighted = 0;const peak={low:0,mid:0,high:0},peakHz={low:80,mid:625,high:1000};
    for (let i=1;i<data.length;i++) {
      const hz=i*sampleRate/fftSize;
      if(hz<20)continue;
      const band=hz<250?'low':hz<1000?'mid':'high';
      // A zero byte represents the analyzer floor, not a measurable signal.
      const power=data[i]===0?0:10**((minDb+data[i]/255*(maxDb-minDb))/10);
      if(hz<=100){bassPower+=power;bassCount++;bassPeak=Math.max(bassPeak,power);}
      sum[band]+=power;count[band]++;if(power>peak[band]){peak[band]=power;peakHz[band]=hz;}
      if(band==='high')weighted+=power*hz;else lowMidWeighted+=power*hz;
    }
    return {bass100:Math.min(1,Math.sqrt(bassPower/Math.max(1,bassCount))*8),bass100Peak:Math.min(1,Math.sqrt(bassPeak)*8),lowHz:peakHz.low,highTriggerHz:peakHz.high,lowPeak:Math.min(1,Math.sqrt(peak.low)*8),highPeak:Math.min(1,Math.sqrt(peak.high)*8),low:Math.min(1,Math.sqrt(sum.low/Math.max(1,count.low))*8),
      mid:Math.min(1,Math.sqrt(sum.mid/Math.max(1,count.mid))*8),
      high:Math.min(1,Math.sqrt(sum.high/Math.max(1,count.high))*8),
      lowMidLevel:Math.min(1,Math.sqrt((sum.low+sum.mid)/Math.max(1,count.low+count.mid))*8),
      lowMidHz:sum.low+sum.mid?lowMidWeighted/(sum.low+sum.mid):20,
      highHz:sum.high?weighted/sum.high:1000};
  }
});
// raVe's waveform peak and 15-frame mean, sampled at its original 60 Hz cadence.
// One result feeds custom motion, diffusion, color and pet scaling.
const BassTrigger={clock:0,last:null,history:Array(15).fill(0),strength:0,spike:false,
 reset(){this.clock=0;this.last=null;this.history=Array(15).fill(0);this.strength=0;this.spike=false;},
 step(samples,dt){
  this.clock+=dt;
  if(this.last!==null&&this.clock-this.last<1/60-.0005)return false;
  this.last=this.clock;
  let peak=0;for(const sample of samples)peak=Math.max(peak,Math.abs(sample));
  this.strength=peak;
  this.history.shift();this.history.push(peak);
  const average=this.history.reduce((sum,value)=>sum+value,0)/15;
  this.spike=peak>.3&&peak>1.3*average;
  return this.spike;
 }
};
const Shepherd = {
  releaseSeconds:.5, pulsePeak:0, pulseAge:.5,
  clock:0, cooldowns:new Map(), low:0, previousLow:0, mid:0, pulse:0, previousSignal:0, high:0, previousHigh:0, credit:0, stars:[], serial:0,
  reset(){this.pulsePeak=0;this.pulseAge=this.releaseSeconds;this.clock=0;this.cooldowns.clear();this.low=this.previousLow=this.mid=this.pulse=this.previousSignal=this.high=this.previousHigh=this.credit=this.serial=0;this.stars=[];},
  stop(){this.reset();},
  step(bands,dt,reduced=false,rms,bassEvent=false){
    dt=Number.isFinite(dt)?Math.max(0,Math.min(.1,dt)):0;
    if(Number.isFinite(rms)&&rms<.0015)bands={bassPeak:bands.bassPeak};
    this.clock+=dt;for(const [key,time]of this.cooldowns)if(this.clock-time>1)this.cooldowns.delete(key);
    const accept=(band,hz)=>{const key=band+':'+Math.round(hz);if(this.clock-(this.cooldowns.get(key)??-Infinity)<.1-1e-9)return false;this.cooldowns.set(key,this.clock);return true;};
    const follow=(a,b,attack,release)=>a+(b-a)*(1-Math.exp(-dt/(b>a?attack:release)));
    // MID is deliberately inactive; LOW uses the shared filtered waveform peak.
    const low=Math.max(0,Math.min(1,bands.bassPeak||0));
    const target=.10+.40*Math.sqrt(Math.min(1,low*3));
    if(bassEvent){this.pulsePeak=target;this.pulseAge=0;}else this.pulseAge+=dt;
    const remaining=Math.max(0,1-this.pulseAge/this.releaseSeconds);
    this.pulse=this.pulsePeak*remaining*remaining*(3-2*remaining);
    this.previousSignal=low;this.low=low;this.mid=0;this.previousLow=low;
    const high=Math.max(0,Math.min(1,Math.max(bands.high||0,(bands.highPeak||0)*.8)));
    const rising=high>0&&accept('high',bands.highTriggerHz||bands.highHz||1000);
    const flux=Math.max(0,high-this.previousHigh)/Math.max(.001,dt);
    this.previousHigh=high;
    this.high=follow(this.high,Math.min(1,high*5+flux*.16),.003,.055);
    const speed=210+420*Math.max(0,Math.min(1,Math.log2(Math.max(1000,bands.highHz||1000)/1000)/4.5));
    // One normalized meteor field is reflected in the lens and painted in the sky.
    // Retain each meteor after its head dies so the luminous train can fade away.
    this.stars=this.stars.filter(s=>{s.age+=dt;return s.age<s.life+.65;});
    const emit=()=>{
      const n=this.serial++;
      // Launch inside the visible watch sky, with a visible head immediately.
      if(this.stars.length>=96)this.stars.shift();
      const angle=n*2.399963,radius=440*Math.sqrt(((n*37)%97+.5)/97);
      this.stars.push({x:500+Math.cos(angle)*radius,y:500+Math.sin(angle)*radius,
        speed:speed*(reduced?.35:1),age:.025,life:.65+(n*17%65)/100});
    };
    // Every detected rise emits immediately; no accumulated-credit threshold.
    if(rising)emit();


  },
  init(){
    this.image=new Image();this.image.src='assets/shepherd.png';
  },
  meteorAlpha(s,emitted){
    const age=s.age-emitted;
    return Math.max(0,1-age/.65)**1.7*Math.min(1,s.age/.06);
  },
  drawMeteors(c,x,y,w,h,dark=false){
    const ink=dark?'0,0,0':'255,255,255';
    c.save();c.translate(x,y);c.scale(w/1000,h/1000);c.lineCap='round';
    for(const s of this.stars){
      const end=Math.min(s.age,s.life),start=Math.max(0,s.age-.65);
      if(start>=end)continue;
      // Each tail point stays at its emission position and fades with its age.
      // Gradient stops give a bright head and a continuous vanishing tail.
      const ax=s.x-s.speed*start*.55,ay=s.y+s.speed*start;
      const bx=s.x-s.speed*end*.55,by=s.y+s.speed*end;
      const g=c.createLinearGradient(ax,ay,bx,by);
      for(const u of [0,.25,.5,.75,1])g.addColorStop(u,`rgba(${ink},${this.meteorAlpha(s,start+(end-start)*u)*.85})`);
      c.strokeStyle=g;c.lineWidth=3.2;c.beginPath();c.moveTo(ax,ay);c.lineTo(bx,by);c.stroke();
      const alpha=Math.max(0,1-Math.max(0,s.age-s.life)/.12)*Math.min(1,s.age/.06);
      if(alpha>0){c.fillStyle=`rgba(${ink},${alpha})`;c.beginPath();c.arc(s.x-s.speed*end*.55,s.y+s.speed*end,2.5,0,Math.PI*2);c.fill();}
    }
    c.restore();
  },
  drawBackground(c,W,H){
    const size=Math.min(W,H);c.save();c.beginPath();c.arc(W/2,H/2,size/2,0,Math.PI*2);c.clip();
    c.fillStyle='#000000';c.fillRect(0,0,W,H);
    c.restore();
  },
  draw(c,W,H,t,active,reduced){
    if(!this.image?.complete||!this.image.naturalWidth)return;
    const size=Math.min(W,H);
    c.save();c.beginPath();c.arc(W/2,H/2,size/2,0,Math.PI*2);c.clip();

    const pulse=active?(reduced?Math.min(.05,this.pulse)+Math.max(0,this.pulse-.05)*.25:this.pulse):0;
    const scale=size*.46/1230*(1+pulse);
    c.translate(W/2,H/2);c.scale(scale,scale);c.translate(-639,-615);
    // Preserve the complete sprite: no MID scanline displacement or rim vibration.
    c.save();c.shadowColor='rgba(255,229,175,.55)';c.shadowBlur=size*.015;
    c.drawImage(this.image,0,0,1278,1230);c.restore();
    c.save();c.beginPath();
    // Lens interior in the original 1278 × 1230 sprite coordinates.
    c.moveTo(793,468);c.bezierCurveTo(816,453,875,451,907,465);c.bezierCurveTo(920,484,908,539,889,551);c.bezierCurveTo(867,566,817,563,806,544);c.bezierCurveTo(796,522,790,488,793,468);c.closePath();c.clip();
    c.fillStyle='#000000';c.fill();
    if(active)this.drawMeteors(c,788,450,130,115);
    else{c.fillStyle='rgba(218,231,246,.25)';c.beginPath();c.moveTo(829,471);c.lineTo(846,471);c.lineTo(819,505);c.closePath();c.fill();}
    c.restore();c.restore();
  }
};
if(typeof Image!=='undefined')Shepherd.init();
if(typeof module!=='undefined')module.exports={AudioBands,BassTrigger,Shepherd};
