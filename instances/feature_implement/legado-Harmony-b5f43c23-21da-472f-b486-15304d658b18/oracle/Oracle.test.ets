import { describe, expect, it } from '@ohos/hypium';
import ChaptersDataSource from '../../../../../../entry/src/main/ets/common/LazyIDataSource/ChaptersDataSource';
import BasicDataSource from '../../../../../../entry/src/main/ets/common/LazyIDataSource/BasicDataSource';

export default function oracleForlegado_Harmony_b5f43c23_21da_472f_b486_15304d658b18() {
  describe('ChaptersDataSource oracle', function () {
    it('preserves inheritance relation', 0, function () {
      const instance = new ChaptersDataSource();
      expect(instance instanceof BasicDataSource).assertTrue();
    })
    it('keeps basic data source API', 0, function () {
      const instance = new ChaptersDataSource();
      expect(typeof instance.totalCount).assertEqual('function');
      expect(typeof instance.getData).assertEqual('function');
      expect(typeof instance.getListData).assertEqual('function');
      expect(typeof instance.addData).assertEqual('function');
      expect(typeof instance.pushData).assertEqual('function');
      expect(typeof instance.pushAllData).assertEqual('function');
      expect(typeof instance.reloadNewData).assertEqual('function');
    })
    it('starts empty', 0, function () {
      const instance = new ChaptersDataSource();
      expect(instance.totalCount()).assertEqual(0);
      expect(instance.getListData().length).assertEqual(0);
    })
  })
}
