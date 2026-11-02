#!/usr/bin/env python3
"""Add lab-only streaming KBN grouped-AVG aggregate to pinned ClickHouse tree."""
from pathlib import Path

source = Path("src/AggregateFunctions/AggregateFunctionAvgKBNLab.cpp")
assert not source.exists(), "lab aggregate already exists; refuse double patch"
source.write_text(r"""
#include <AggregateFunctions/AggregateFunctionFactory.h>
#include <AggregateFunctions/IAggregateFunction.h>
#include <AggregateFunctions/Helpers.h>
#include <AggregateFunctions/FactoryHelpers.h>
#include <Columns/ColumnsNumber.h>
#include <DataTypes/DataTypesNumber.h>
#include <IO/ReadHelpers.h>
#include <IO/WriteHelpers.h>
#include <cmath>
#include <limits>

namespace DB
{
struct Settings;
namespace ErrorCodes
{
    extern const int ILLEGAL_TYPE_OF_ARGUMENT;
}

struct AvgKBNLabState
{
    Float64 sum = 0;
    Float64 compensation = 0;
    UInt64 count = 0;

    // Same Neumaier correction as TimeSeriesCompensatedSum::Summary.
    void addCompensated(Float64 value)
    {
        const Float64 t = sum + value;
        if (!std::isfinite(t))
            compensation = 0;
        else if (std::abs(sum) >= std::abs(value))
            compensation += (sum - t) + value;
        else
            compensation += (value - t) + sum;
        sum = t;
    }

    void add(Float64 value)
    {
        addCompensated(value);
        ++count;
    }

    void merge(const AvgKBNLabState & rhs)
    {
        if (rhs.count == 0)
            return;
        addCompensated(rhs.sum);
        compensation = std::isfinite(sum) ? compensation + rhs.compensation : 0;
        count += rhs.count;
    }

    Float64 result() const
    {
        return count ? (sum + compensation) / static_cast<Float64>(count)
                     : std::numeric_limits<Float64>::quiet_NaN();
    }
};

class AggregateFunctionAvgKBNLab final
    : public IAggregateFunctionDataHelper<AvgKBNLabState, AggregateFunctionAvgKBNLab>
{
public:
    explicit AggregateFunctionAvgKBNLab(const DataTypes & types, const Array & params)
        : IAggregateFunctionDataHelper<AvgKBNLabState, AggregateFunctionAvgKBNLab>(
              types, params, std::make_shared<DataTypeFloat64>())
    {
    }

    String getName() const override { return "avgKBNLab"; }
    bool allocatesMemoryInArena() const override { return false; }

    void add(AggregateDataPtr __restrict place, const IColumn ** columns,
             size_t row_num, Arena *) const override
    {
        data(place).add(assert_cast<const ColumnFloat64 &>(*columns[0]).getData()[row_num]);
    }

    void mergeImpl(AggregateDataPtr __restrict place,
                   ConstAggregateDataPtr rhs, Arena *) const override
    {
        data(place).merge(data(rhs));
    }

    void serialize(ConstAggregateDataPtr __restrict place, WriteBuffer & buf,
                   std::optional<size_t>) const override
    {
        const auto & state = data(place);
        writeBinary(state.sum, buf);
        writeBinary(state.compensation, buf);
        writeBinary(state.count, buf);
    }

    void deserialize(AggregateDataPtr __restrict place, ReadBuffer & buf,
                     std::optional<size_t>, Arena *) const override
    {
        auto & state = data(place);
        readBinary(state.sum, buf);
        readBinary(state.compensation, buf);
        readBinary(state.count, buf);
    }

    void insertResultInto(AggregateDataPtr __restrict place, IColumn & to, Arena *) const override
    {
        assert_cast<ColumnFloat64 &>(to).getData().push_back(data(place).result());
    }
};

namespace
{
AggregateFunctionPtr createAvgKBNLab(
    const std::string & name, const DataTypes & types,
    const Array & params, const Settings *)
{
    assertNoParameters(name, params);
    assertUnary(name, types);
    if (types[0]->getTypeId() != TypeIndex::Float64)
        throw Exception(ErrorCodes::ILLEGAL_TYPE_OF_ARGUMENT,
                        "The lab function {} requires Float64, got {}",
                        name, types[0]->getName());
    return std::make_shared<AggregateFunctionAvgKBNLab>(types, params);
}
}

void registerAggregateFunctionAvgKBNLab(AggregateFunctionFactory & factory);
void registerAggregateFunctionAvgKBNLab(AggregateFunctionFactory & factory)
{
    FunctionDocumentation::Description desc =
        "Laboratory-only streaming Kahan-Babuska-Neumaier mean of Float64 values.";
    FunctionDocumentation::Syntax syntax = "avgKBNLab(value)";
    FunctionDocumentation::Arguments arguments =
        {{"value", "Float64 value.", {"Float64"}}};
    FunctionDocumentation::ReturnedValue returned =
        {"Mean of values, NaN if empty.", {"Float64"}};
    FunctionDocumentation::IntroducedIn introduced = {26, 11};
    FunctionDocumentation::Category category = FunctionDocumentation::Category::AggregateFunction;
    FunctionDocumentation doc = {desc, syntax, arguments, {}, returned, {}, introduced, category};
    factory.registerFunction("avgKBNLab", {createAvgKBNLab, doc});
}
}
""".lstrip())

path = Path("src/AggregateFunctions/registerAggregateFunctions.cpp")
body = path.read_text()
old = "void registerAggregateFunctionAvg(AggregateFunctionFactory &);\n"
assert body.count(old) == 1
body = body.replace(old, old + "void registerAggregateFunctionAvgKBNLab(AggregateFunctionFactory &);\n", 1)
old = "        registerAggregateFunctionAvg(factory);\n"
assert body.count(old) == 1
body = body.replace(old, old + "        registerAggregateFunctionAvgKBNLab(factory);\n", 1)
path.write_text(body)
print("AVG_KBN_LAB_CPP_APPLIED", source, path)
