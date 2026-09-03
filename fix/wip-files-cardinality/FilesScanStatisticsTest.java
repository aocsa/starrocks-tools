// Copyright 2021-present StarRocks, Inc. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.starrocks.sql.plan;

import com.starrocks.catalog.TableFunctionTable;
import com.starrocks.fs.HdfsUtil;
import com.starrocks.sql.StatementPlanner;
import com.starrocks.sql.ast.StatementBase;
import com.starrocks.thrift.TExplainLevel;
import com.starrocks.thrift.THdfsProperties;
import com.starrocks.utframe.UtFrameUtils;
import mockit.Invocation;
import mockit.Mock;
import mockit.MockUp;
import org.apache.commons.lang3.StringUtils;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * FILES() scans reach the CBO with a row count instead of the historical 1 row, so join sides and the
 * broadcast/shuffle choice are decided from data volume. Column statistics stay unknown.
 *
 * Each FILES() scan is named through a CTE, the way the TPC-H bench queries do it
 * (WITH lineitem AS (SELECT * FROM FILES(...))); consumed once, a CTE is inlined into the plan.
 */
public class FilesScanStatisticsTest extends PlanTestBase {

    private static String files(String name) {
        return name + " as (select * from files('path'='fake://" + name + "/*','format'='parquet'))";
    }

    /**
     * EXPLAIN COSTS of one statement. Not getCostExplain(): UtFrameUtils.getPlanAndFragment also prints the
     * optimizer tree with LogicalPlanPrinter, which has no arm for PhysicalTableFunctionTableScanOperator and
     * recurses until StackOverflowError (an upstream test-utility gap unrelated to statistics).
     */
    private static String costExplain(String sql) throws Exception {
        connectContext.setThreadLocalInfo();
        StatementBase statement = UtFrameUtils.parseStmtWithNewParser(sql, connectContext);
        ExecPlan plan = StatementPlanner.plan(statement, connectContext);
        return plan.getExplainString(TExplainLevel.COSTS);
    }

    @BeforeEach
    public void mockFakeFileSystem() {
        // fake:// lists its two files inside TableFunctionTable, but building the physical FileScanNode still asks
        // HDFS for the path's properties, and no Hadoop FileSystem serves the fake scheme.
        new MockUp<HdfsUtil>() {
            @Mock
            public static void getTProperties(String path, Map<String, String> properties, THdfsProperties tProperties) {
            }
        };
    }

    @Test
    public void testFilesScanCardinalityInExplainCosts() throws Exception {
        String plan = costExplain("with " + files("a") + ", " + files("b")
                + " select a.col_int from a join b on a.col_int = b.col_int");
        // fake:// lists 1024 + 2048 B with the schema col_int INT, col_string VARCHAR: 3072 / 20 = 153 rows per scan.
        Assertions.assertTrue(StringUtils.countMatches(plan, "cardinality: 153\n") >= 2, plan);
        Assertions.assertFalse(plan.contains("cardinality: 1\n"), plan);
    }

    @Test
    public void testInjectedRowCountsDecideJoinSide() throws Exception {
        new MockUp<TableFunctionTable>() {
            @Mock
            public long estimateRowCount(Invocation inv) {
                TableFunctionTable table = inv.getInvokedInstance();
                return table.getPath().contains("big") ? 1_000_000_000L : 10L;
            }
        };
        String with = "with " + files("big") + ", " + files("small") + " ";

        String plan = costExplain(with + "select count(*) from big b join small s on b.col_int = s.col_int");
        assertContains(plan, "join op: INNER JOIN (BROADCAST)");
        // The 10-row scan is the build side: it feeds the EXCHANGE that is the join's right child.
        Assertions.assertTrue(buildSideFragment(plan).contains("cardinality: 10\n"), plan);
        Assertions.assertTrue(joinFragment(plan).contains("cardinality: 1000000000\n"), plan);

        // With a 10-row outer side the CBO commutes LEFT SEMI to RIGHT SEMI so the small side builds, so a
        // compute node that consumes these plans has to execute RIGHT_SEMI_JOIN.
        String semi = costExplain(with + "select count(*) from small s where s.col_int in (select col_int from big)");
        assertContains(semi, "RIGHT SEMI JOIN");
    }

    /** Text of the plan fragment holding the HASH JOIN. */
    private static String joinFragment(String plan) {
        for (String fragment : plan.split("PLAN FRAGMENT ")) {
            if (fragment.contains("HASH JOIN")) {
                return fragment;
            }
        }
        Assertions.fail("no HASH JOIN in plan:\n" + plan);
        return "";
    }

    /** Text of the plan fragment whose sink feeds the join's right (build) child EXCHANGE. */
    private static String buildSideFragment(String plan) {
        Matcher child = Pattern.compile("\\|----(\\d+):EXCHANGE").matcher(joinFragment(plan));
        Assertions.assertTrue(child.find(), "join has no EXCHANGE right child:\n" + plan);
        int exchangeId = Integer.parseInt(child.group(1));
        // COSTS prints the sink as "OutPut Exchange Id: 02"; NORMAL prints "EXCHANGE ID: 02".
        Pattern sink = Pattern.compile("(?i)(OutPut Exchange Id|EXCHANGE ID): 0*" + exchangeId + "\\b");
        for (String fragment : plan.split("PLAN FRAGMENT ")) {
            if (sink.matcher(fragment).find() && !fragment.contains("HASH JOIN")) {
                return fragment;
            }
        }
        Assertions.fail("no fragment sinks into exchange " + exchangeId + ":\n" + plan);
        return "";
    }
}
