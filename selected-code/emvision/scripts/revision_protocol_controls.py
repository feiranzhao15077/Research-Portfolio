"""Independent test-set controls for the corrected receiver-noise protocol."""
import json
import os
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"scripts"))
os.environ.setdefault("MPLCONFIGDIR",str(ROOT/".mplcache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from demo_sheath_microdoppler import _gen_sheath_set
from src.recognition import MLP, accuracy
from src.recognition_data import standardize
from src.experiment_protocol import metadata, sample_sd, mean_ci95
FP=(0.,4.,8.,9.5,12.,14.)
SEEDS=tuple(range(6))
def fit(X,y,seed):
    mu=X.mean(axis=0); sd=X.std(axis=0)+1e-12
    m=MLP(3,hidden=32,lr=.08,epochs=200,batch=32,seed=seed).fit((X-mu)/sd,y)
    return m,mu,sd
def score(model,X,y):
    m,mu,sd=model
    return float(accuracy(y,m.predict((X-mu)/sd)))
def summary(v):
    m=float(np.mean(v)); s=sample_sd(v)
    return dict(raw_runs=v,mean=m,sample_sd=s,ci95=mean_ci95(m,s,len(v)))
def main():
    fixed={str(fp):[] for fp in FP}
    retrain={str(fp):[] for fp in FP}
    shape={str(fp):[] for fp in FP}
    noise=[]; shuffled=[]
    keep=[0,1,4,5,6]
    for seed in SEEDS:
        for fp in FP:
            # Disjoint RNG streams: training and independent test population.
            X,y=_gen_sheath_set(200,fp,2.4,seed=seed)
            Xt,yt=_gen_sheath_set(50,fp,2.4,seed=1000+seed)
            fitted=fit(X,y,seed)
            if fp==0:
                frozen=fitted
                perm=np.random.default_rng(2000+seed).permutation(y)
                shuffled.append(score(fit(X,perm,seed),Xt,yt))
            fixed[str(fp)].append(score(frozen,Xt,yt))
            retrain[str(fp)].append(score(fitted,Xt,yt))
            shape[str(fp)].append(score(fit(X[:,keep],y,seed),Xt[:,keep],yt))
        X,y=_gen_sheath_set(200,0,2.4,seed=seed,signal_gain=0)
        Xt,yt=_gen_sheath_set(50,0,2.4,seed=1000+seed,signal_gain=0)
        noise.append(score(fit(X,y,seed),Xt,yt))
        print("control seed",seed,"noise",noise[-1],"fixed fp14",fixed["14.0"][-1],flush=True)
    out=dict(fp_ghz=list(FP),seeds=list(SEEDS),n_train_per_class=200,n_test_per_class=50,
        train_seed=list(SEEDS),test_seed=[1000+s for s in SEEDS],
        note="Independent synthetic test populations. Fixed models fit only at fp=0; per-condition models retrain. No measured data.",
        chance=1/3,noise_only=summary(noise),shuffled_train_labels=summary(shuffled),
        fixed_model={k:summary(v) for k,v in fixed.items()},
        per_condition={k:summary(v) for k,v in retrain.items()},
        without_absolute_envelope={k:summary(v) for k,v in shape.items()},
        retained_features=keep,protocol=metadata())
    out["protocol"]["split"]="independent train/test generation; no random holdout"
    out["protocol"]["training"]="separate fixed-model, per-condition, feature-ablation, and negative-control arms"
    path=ROOT/"data/protocol_controls.json"
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    plt.rcParams["font.sans-serif"]=["Microsoft YaHei","SimHei"]
    plt.rcParams["axes.unicode_minus"]=False
    fig,ax=plt.subplots(figsize=(7.5,4.6))
    for key,label,fmt in (("fixed_model","固定模型（仅 fp=0 训练）","o-"),
                           ("per_condition","各工况重新训练","s-"),
                           ("without_absolute_envelope","各工况重训：移除包络均值/SD","^--")):
        vals=[out[key][str(fp)] for fp in FP]
        ax.errorbar(FP,[100*v["mean"] for v in vals],yerr=[100*v["sample_sd"] for v in vals],fmt=fmt,capsize=3,label=label)
    ax.axhline(100/3,color="black",ls=":",label="三类均衡机会水平")
    ax.set(xlabel="等离子体频率 fp (GHz)",ylabel="识别率 (%)",ylim=(0,105),
           title="独立测试集：训练协议与特征消融（6 次；均值 ± 样本 SD）")
    ax.legend(fontsize=8); ax.grid(alpha=.2);fig.tight_layout()
    for ext in ("png","svg"):
        fig.savefig(ROOT/("figures/fig50_protocol_controls."+ext),dpi=300)
    plt.close(fig)
    print("noise-only",out["noise_only"],"label shuffle",out["shuffled_train_labels"],flush=True)
if __name__=="__main__": main()
