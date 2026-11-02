// Copy to handout/model-src/examples/flynative.rs then:
//   cd handout/model-src && cargo run --release --example flynative -- ../../handout/model.fly
// Standard input: one line per oracle turn, d1 d2 and 28 board integers separated by spaces.
// Standard output: one JSON object per input line. Uses unmodified engine and agent from handout.
use std::io::{self, BufRead, Write};
use std::env;
use flygammon::engine::{self, Board};
use flygammon::agent::choose_2ply;
use flygammon::model::FlyNet;
use flygammon::train::InferBackend;
use flygammon::model::FlyNetConfig;
use burn::module::{Module,Param};
use burn::tensor::TensorData;

fn process(line: &str, net: &FlyNet<InferBackend>, dev: &burn::backend::ndarray::NdArrayDevice) -> Result<String,String> {
    let toks: Vec<&str> = line.split_whitespace().collect();
    if toks.len()!=30 {return Err(format!("expected 30 numbers, got {}",toks.len()));}
    let nums: Vec<i16> = toks.iter().map(|s|s.parse::<i16>().map_err(|e|e.to_string())).collect::<Result<_,_>>()?;
    let dice=[nums[0] as u8,nums[1] as u8];
    let mut pts=[0i8;24];
    for i in 0..24 {pts[i]=nums[i+2] as i8;}
    let board=Board {points:pts, bar:[nums[26] as u8,nums[27] as u8], off:[nums[28] as u8,nums[29] as u8]};
    let choice = choose_2ply(net,&board,dice,dev);
    let (end,path)=match choice {
        Some(end) => {
            let path=engine::path_to(&board,dice,&end).ok_or("No path to chosen board")?;
            (end,path)
        },
        None => (board,Vec::new()),
    };
    let froms:Vec<u8>=path.iter().map(|x|x.0).collect();
    let dies:Vec<u8>=path.iter().map(|x|x.1).collect();
    let mut v:Vec<i16>=end.points.iter().map(|x|*x as i16).collect();
    v.extend(end.bar.iter().map(|x|*x as i16));
    v.extend(end.off.iter().map(|x|*x as i16));
    Ok(format!("{{\"froms\":{:?},\"dies\":{:?},\"end\":{:?}}}",froms,dies,v))
}

fn main() {
    let path=env::args().nth(1).expect("Usage: flynative /path/to/model.fly [hidden=80]");
    let hidden=env::args().nth(2).and_then(|s|s.parse::<usize>().ok()).unwrap_or(80);
    // Handout model.fly contains exactly 23205 little-endian f32, NOT
    // Burn CompactRecorder / NamedMpk (which expects an .mpk checkpoint).
    // Reconstruct a native Burn FlyNet record in model.rs layer order.
    let bytes=std::fs::read(&path).expect("read raw model.fly");
    if bytes.len()!=23205*4 {panic!("expected raw model.fly with 92820 bytes, got {}",bytes.len());}
    let weights:Vec<f32>=bytes.chunks_exact(4).map(|x|
        f32::from_le_bytes([x[0],x[1],x[2],x[3]])).collect();
    if hidden!=80 {panic!("handout model.fly uses hidden=80");}
    let dev=burn::backend::ndarray::NdArrayDevice::default();
    let model:FlyNet<InferBackend>=FlyNetConfig::new().with_hidden(hidden).init(&dev);
    let mut rec=model.clone().into_record();
    let mut i=0usize;
    fn grab(w:&[f32], at:&mut usize, n:usize)->Vec<f32>{
        let out=w[*at..*at+n].to_vec(); *at+=n;out
    }
    rec.l1.weight=Param::from_data(TensorData::new(grab(&weights,&mut i,203*80),[203,80]),&dev);
    rec.l1.bias=Some(Param::from_data(TensorData::new(grab(&weights,&mut i,80),[80]),&dev));
    rec.l2.weight=Param::from_data(TensorData::new(grab(&weights,&mut i,80*80),[80,80]),&dev);
    rec.l2.bias=Some(Param::from_data(TensorData::new(grab(&weights,&mut i,80),[80]),&dev));
    rec.l3.weight=Param::from_data(TensorData::new(grab(&weights,&mut i,80*5),[80,5]),&dev);
    rec.l3.bias=Some(Param::from_data(TensorData::new(grab(&weights,&mut i,5),[5]),&dev));
    assert_eq!(i,weights.len());
    let net=model.load_record(rec);
    let stdin=io::stdin();
    for line in stdin.lock().lines() {
        let line=match line {Ok(x)=>x,Err(_)=>break};
        if line.trim().is_empty(){continue;}
        let reply=match process(&line,&net,&dev){Ok(x)=>x,Err(e)=>format!("{{\"error\":{:?}}}",e)};
        println!("{}",reply);
        let _=io::stdout().flush();
    }
}
