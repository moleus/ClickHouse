#!/usr/bin/env python3
"""Standalone negative control: incremental compensated mean after sum overflow.

This is not a ClickHouse or Prometheus binary implementation. It tests
whether a tempting new C++ algorithm would meet an accuracy objective.
"""
import argparse, decimal, json, math, pathlib
from dataclasses import dataclass

def compensated_add(s,c,x):
    t=s+x
    if not math.isfinite(t): return t,0.
    if abs(s)>=abs(x): c+=(s-t)+x
    else: c+=(x-t)+s
    return t,c

@dataclass
class OnlineMean:
    summation:float=0.
    correction:float=0.
    n:int=0
    mean:float=0.
    mean_correction:float=0.
    incremental:bool=False
    def result(self):
        if not self.n:return float('nan')
        if self.incremental:return self.mean+self.mean_correction
        return self.summation/self.n+self.correction/self.n
    def add(self,x):
        if self.n==0:
            self.summation=x;self.n=1
            return
        new_n=self.n+1
        if not self.incremental:
            t=self.summation+x
            if math.isfinite(t):
                self.summation,self.correction=compensated_add(
                    self.summation,self.correction,x)
                self.n=new_n
                return
            self.incremental=True
            self.mean=self.summation/self.n
            self.mean_correction=self.correction/self.n
        fraction=self.n/new_n
        self.mean,self.mean_correction=compensated_add(
            fraction*self.mean,fraction*self.mean_correction,x/new_n)
        self.n=new_n
    def merge(self,other):
        if not other.n:return
        if not self.n:
            self.__dict__.update(other.__dict__)
            return
        n=self.n+other.n
        if (not self.incremental and not other.incremental and
            math.isfinite(self.summation+other.summation)):
            self.summation,self.correction=compensated_add(
                self.summation,self.correction,other.summation)
            if math.isfinite(self.summation):
                self.correction+=other.correction
            self.n=n
            return
        self_mean=self.result()
        other_mean=other.result()
        self.mean,self.mean_correction=compensated_add(
            (self.n/n)*self_mean,0.,(other.n/n)*other_mean)
        self.incremental=True
        self.n=n

def cases():
    return {
      "same_large":[1e308,1e308],
      "many_large":[1e308]*99,
      "four_cancel":[1e308,1e308,-1e308,-1e308],
      "huge_opposite_halves":[1e308]*100+[-1e308]*100,
      "small_residual_after_overflow":[1e308]*100+[-1e308]*100+[1e100]*5,
      "three_small":[1e18,1.0,-1e18],
      "many_small":[1e18]+[1.0]*127+[-1e18],
    }
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    decimal.getcontext().prec=1000
    out=[]
    for name,values in cases().items():
        d=decimal.Decimal
        oracle=float(sum((d.from_float(v) for v in values),d(0))/d(len(values)))
        plain=OnlineMean()
        for value in values:plain.add(value)
        chunks=[]
        for i in range(0,len(values),3):
            state=OnlineMean()
            for value in values[i:i+3]:state.add(value)
            chunks.append(state)
        merged=OnlineMean()
        for chunk in chunks:merged.merge(chunk)
        scale=max(1.,max(map(abs,values)))
        a=plain.result();b=merged.result()
        result={"case":name,"n":len(values),"oracle":oracle,
                "sequential":a,"merged":b,
                "seq_abs_error":abs(a-oracle),"merge_abs_error":abs(b-oracle),
                "seq_scaled_error":abs(a-oracle)/scale,
                "merge_scaled_error":abs(b-oracle)/scale,
                "fallback_used":plain.incremental}
        out.append(result)
        print("OVERFLOW_MEAN_MODEL_CASE",json.dumps(result,separators=(",",":")),flush=True)
    r={v["case"]:v for v in out}
    assert r["same_large"]["sequential"]==r["same_large"]["oracle"]
    assert r["four_cancel"]["sequential"]==0
    assert r["three_small"]["sequential"]==r["three_small"]["oracle"]
    # The absolute error is catastrophic in comparison with a small true residual,
    # even though it is around floating-point epsilon of the huge input scale.
    counterexample=r["small_residual_after_overflow"]
    assert 1e97<counterexample["oracle"]<1e100
    assert counterexample["seq_abs_error"]>1e200
    assert counterexample["merge_abs_error"]>1e200
    pathlib.Path(args.output).write_text(json.dumps({
        "model_only":True,"simple_overflow_repaired":True,
        "small_residual_still_lost":True,"cases":out},indent=2)+"\n")
    print("OVERFLOW_MEAN_MODEL_RESULT",json.dumps({
        "simple_overflow_repaired":True,"small_residual_still_lost":True,
        "cases":len(out)},separators=(",",":")),flush=True)
if __name__=="__main__":main()
