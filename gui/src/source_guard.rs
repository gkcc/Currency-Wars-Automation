//! Shared local byte gate. No controller, image decoding, or process launch.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, fs, path::{Path, PathBuf}};

pub const INVENTORY_PATH: &str = "tools/currency_wars_runtime_sources.json";
pub const EMBEDDED_INVENTORY: &str = include_str!("../../tools/currency_wars_runtime_sources.json");

fn relative(value: &Value) -> Result<&str, String> {
    let name=value.as_str().ok_or("来源路径不是文本")?;
    if name.is_empty() || name.contains('\\') || name.contains(':') || name.split('/').any(|p|p.is_empty()||p=="."||p=="..") {
        return Err("来源路径必须是安全的相对路径".into());
    }
    Ok(name)
}
fn unlinked(path: &Path) -> Result<fs::Metadata,String> {
    let metadata=fs::symlink_metadata(path).map_err(|_|"来源路径缺失".to_string())?;
    if metadata.file_type().is_symlink(){return Err("来源路径包含链接".into());}
    #[cfg(windows)] {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {return Err("来源路径包含重解析点".into());}
    }
    Ok(metadata)
}
fn bytes(project:&Path,name:&str)->Result<Vec<u8>,String>{
    let path=project.join(name);
    for ancestor in path.ancestors(){unlinked(ancestor)?;if ancestor==project{break;}}
    let metadata=unlinked(&path)?;
    if !metadata.is_file() || metadata.len()==0 || metadata.len()>4_000_000{return Err(format!("来源大小或路径无效：{name}"));}
    fs::read(path).map_err(|_|format!("来源不可读：{name}"))
}
fn digest(data:&[u8])->String{format!("{:X}",Sha256::digest(data))}
fn array<'a>(value:&'a Value,key:&str)->Result<&'a Vec<Value>,String>{value[key].as_array().ok_or_else(||format!("来源清单缺少 {key}"))}
fn resource_files(project:&Path,path:&Path,result:&mut Vec<String>)->Result<(),String>{
    for entry in fs::read_dir(path).map_err(|_|"素材目录不可读")?{
        let path=entry.map_err(|_|"素材项不可读")?.path();
        let metadata=unlinked(&path)?;
        if metadata.is_dir(){resource_files(project,&path,result)?;}
        else{
            let name=path.strip_prefix(project).map_err(|_|"素材越过项目根")?.to_string_lossy().replace('\\',"/");
            result.push(name);
            if result.len()>512{return Err("素材数量超过上限".into());}
        }
    }
    Ok(())
}

/// Caller verifies READY review/session ownership and authenticated launcher record.
pub fn verify_runtime_sources(project:&Path,ready:&Value,selected_provider:&Value,embedded:&str)->Result<(),String>{
    if bytes(project,INVENTORY_PATH)?!=embedded.as_bytes(){return Err("来源清单与 GUI 内嵌版本不同，请重新构建并核验".into());}
    let manifest:Value=serde_json::from_str(embedded).map_err(|_|"来源清单格式无效")?;
    if manifest["schema"]!=1{return Err("来源清单版本无效".into());}
    let files=array(&manifest,"files")?;
    if files.is_empty()||files.len()>128{return Err("源码数量无效".into());}
    let mut hashes=BTreeMap::<String,String>::new();
    for value in files{
        let name=relative(value)?;
        if hashes.insert(name.into(),digest(&bytes(project,name)?)).is_some(){return Err("来源路径重复".into());}
    }
    if !hashes.contains_key(INVENTORY_PATH){return Err("来源清单未绑定自身".into());}
    let roots=array(&manifest,"resource_roots")?;
    if roots.is_empty(){return Err("素材根清单缺失".into());}
    let mut inventory=serde_json::Map::new();
    for spec in roots{
        let name=relative(&spec["path"])?;
        let required=spec["required"].as_bool().ok_or("素材必要性未知")?;
        if inventory.contains_key(name){return Err("素材根重复".into());}
        let root=project.join(name);
        let present=match fs::symlink_metadata(&root){Ok(_)=>true,Err(e) if e.kind()==std::io::ErrorKind::NotFound=>false,Err(_)=>return Err("素材根不可读".into())};
        let mut members=Vec::new();
        if !present&&required{return Err(format!("必要素材根缺失：{name}"));}
        if present{
            if !unlinked(&root)?.is_dir(){return Err("素材根不是目录".into());}
            resource_files(project,&root,&mut members)?;
            members.sort();
            for item in &members{hashes.insert(item.clone(),digest(&bytes(project,item)?));}
        }
        inventory.insert(name.into(),json!({"present":present,"files":members}));
    }
    let declarations=array(&manifest,"resource_manifests")?;
    if declarations.is_empty(){return Err("素材来源清单缺失".into());}
    for spec in declarations{
        let name=relative(&spec["path"])?;
        if !hashes.contains_key(name){return Err("素材来源文件缺失".into());}
        let declared:Value=serde_json::from_slice(&bytes(project,name)?).map_err(|_|"素材来源格式无效")?;
        let entries=match spec["layout"].as_str(){Some("resources")=>array(&declared,"resources")?.clone(),Some("single")=>vec![declared],_=>return Err("素材清单布局无效".into())};
        if entries.is_empty()||entries.len()>512{return Err("素材清单数量无效".into());}
        let mut names=BTreeSet::new();
        for entry in entries{
            let file=relative(&entry["file"])?;
            if !names.insert(file.to_owned()){return Err("素材声明重复".into());}
            let key=Path::new(name).parent().ok_or("素材清单没有父目录")?.join(file).to_string_lossy().replace('\\',"/");
            let hash=hashes.get(&key).ok_or("声明的素材缺失")?;
            if let Some(expected)=entry.get("sha256"){
                if !expected.as_str().unwrap_or("").eq_ignore_ascii_case(hash){return Err("素材与原始来源摘要不符".into());}
            }
        }
        for required in array(spec,"required_files")?{
            if !names.contains(relative(required)?){return Err("素材清单漏了必要文件".into());}
        }
    }
    for value in array(&manifest,"resource_files")?{
        if !hashes.contains_key(relative(value)?){return Err("必要素材元数据缺失".into());}
    }
    if ready["resource_inventory"]!=Value::Object(inventory){return Err("素材成员与本机审查记录不符".into());}
    let reviewed=ready["hashes"].as_object().ok_or("源码与素材摘要缺失")?;
    if reviewed.len()!=hashes.len(){return Err("来源集合与本机审查记录不符".into());}
    for (name,hash) in hashes{
        if !reviewed.get(&name).and_then(Value::as_str).unwrap_or("").eq_ignore_ascii_case(&hash){return Err(format!("来源字节变化：{name}"));}
    }
    let provider=selected_provider.as_object().ok_or("已选择的生命周期来源缺失")?;
    if provider.len()!=3 || !matches!(selected_provider["kind"].as_str(),Some("installed")|Some("standalone")) || ready["runtime_provider"]!=*selected_provider{
        return Err("生命周期来源与已验证启动器选择不符".into());
    }
    let path=PathBuf::from(selected_provider["path"].as_str().ok_or("生命周期来源路径缺失")?);
    if !path.is_absolute(){return Err("生命周期来源不是绝对路径".into());}
    for ancestor in path.ancestors(){unlinked(ancestor)?;}
    let parent=path.parent().ok_or("生命周期来源没有父目录")?;
    let filename=path.file_name().and_then(|s|s.to_str()).ok_or("生命周期来源文件名无效")?;
    if selected_provider["sha256"].as_str()!=Some(digest(&bytes(parent,filename)?).as_str()){
        return Err("生命周期来源字节变化".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests{
    use super::*;
    struct Fixture(PathBuf);
    impl Drop for Fixture{fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
    fn put(root:&Path,name:&str,payload:&[u8]){let p=root.join(name);fs::create_dir_all(p.parent().unwrap()).unwrap();fs::write(p,payload).unwrap();}
    #[test]
    fn reviewed_runtime_sources_require_full_current_inventory(){
        let nonce=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let fixture=Fixture(std::env::temp_dir().join(format!("currency-wars-source-contract-{}-{nonce}",std::process::id())));
        let root=&fixture.0;
        let embedded=json!({"schema":1,"files":[INVENTORY_PATH,"tools/currency_wars_refresh_offer.py"],
            "resource_roots":[{"path":"tools/shop_reader_resources","required":true}],
            "resource_manifests":[{"path":"tools/shop_reader_resources/SOURCES.json","layout":"resources","required_files":["recommend_badge.png"]}],
            "resource_files":["tools/shop_reader_resources/names.json"]}).to_string();
        put(root,INVENTORY_PATH,embedded.as_bytes());
        put(root,"tools/currency_wars_refresh_offer.py",b"# explicit inert protocol fixture\n");
        put(root,"tools/shop_reader_resources/SOURCES.json",br#"{"resources":[{"file":"recommend_badge.png"}]}"#);
        put(root,"tools/shop_reader_resources/names.json",b"{}");
        put(root,"tools/shop_reader_resources/recommend_badge.png",b"Declared byte fixture, not an image recognition sample");
        put(root,"provider.py",b"# inert provider, never imported\n");
        let mut hashes=serde_json::Map::new();
        let members=vec!["tools/shop_reader_resources/SOURCES.json","tools/shop_reader_resources/names.json","tools/shop_reader_resources/recommend_badge.png"];
        for name in [INVENTORY_PATH,"tools/currency_wars_refresh_offer.py"].into_iter().chain(members.iter().copied()){
            hashes.insert(name.into(),json!(digest(&bytes(root,name).unwrap())));
        }
        let provider=json!({"kind":"installed","path":root.join("provider.py"),"sha256":digest(&bytes(root,"provider.py").unwrap())});
        let ready=json!({"hashes":hashes,"runtime_provider":provider,"resource_inventory":{"tools/shop_reader_resources":{"present":true,"files":members}}});
        assert!(verify_runtime_sources(root,&ready,&provider,&embedded).is_ok());
        for missing in ["tools/currency_wars_refresh_offer.py","tools/shop_reader_resources/recommend_badge.png","tools/shop_reader_resources/SOURCES.json"]{
            let path=root.join(missing);let payload=fs::read(&path).unwrap();fs::remove_file(&path).unwrap();
            assert!(verify_runtime_sources(root,&ready,&provider,&embedded).is_err(),"{missing}");fs::write(path,payload).unwrap();
            let mut unreviewed=ready.clone();unreviewed["hashes"].as_object_mut().unwrap().remove(missing);
            assert!(verify_runtime_sources(root,&unreviewed,&provider,&embedded).is_err());
        }
        let asset=root.join("tools/shop_reader_resources/recommend_badge.png");let saved=fs::read(&asset).unwrap();fs::write(&asset,b"Changed bytes").unwrap();
        assert!(verify_runtime_sources(root,&ready,&provider,&embedded).is_err());fs::write(asset,saved).unwrap();
        assert!(verify_runtime_sources(root,&ready,&provider,&format!("{embedded}\n")).is_err());
        fs::write(root.join("provider.py"),b"# changed provider\n").unwrap();
        assert!(verify_runtime_sources(root,&ready,&provider,&embedded).is_err());
    }
}
